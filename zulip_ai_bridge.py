#!/usr/bin/env python3
"""
Zulip to local AI CLI bridge.
Listens for messages on Zulip, runs the local `ai` agent, and posts responses back.

Each message is dispatched to its own daemon thread so the Zulip listener loop
is never blocked — you can send new messages while a previous one is still being
processed by the agent.
"""

import subprocess
import threading
import sys
import os
import logging
try:
    import zulip
except ImportError:
    import types
    zulip = types.ModuleType("zulip")
    zulip.Client = None
    sys.modules["zulip"] = zulip
import re
import requests
from urllib.parse import unquote, quote
import csv
import io

# Configure logging for truncation events
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("ZulipBridge")

# Maximum file size to process (10 MB)
MAX_FILE_SIZE = 10 * 1024 * 1024

# MIME type to content handler mapping
TEXT_EXTS = {'.txt', '.md', '.rst', '.py', '.js', '.ts', '.jsx', '.tsx',
             '.c', '.h', '.cpp', '.hpp', '.java', '.go', '.rs', '.rb',
             '.sh', '.bash', '.zsh', '.bat', '.cmd', '.ps1',
             '.yaml', '.yml', '.toml', '.ini', '.cfg', '.conf',
             '.json', '.xml', '.html', '.htm', '.css', '.scss',
             '.sql', '.r', '.m', '.mm', '.swift', '.kt', '.kts'}

IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp', '.tiff'}

PDF_EXTS = {'.pdf'}

SPREADSHEET_EXTS = {'.xlsx', '.xls', '.csv'}

ARCHIVE_EXTS = {'.zip', '.tar', '.gz', '.bz2', '.xz', '.7z'}


class FileParser:
    """
    Downloads Zulip uploads and extracts text content for various file types.
    """

    def __init__(self, client, base_url):
        self.client = client
        self.base_url = base_url.rstrip('/')
        # Try to import optional dependencies
        self._pdfplumber = None
        self._tesseract = None
        self._pillow = None
        try:
            import pdfplumber
            self._pdfplumber = pdfplumber
        except ImportError:
            pass
        try:
            import pypdfium2 as pdfium
            self._pdfium = pdfium
        except ImportError:
            pass
        try:
            from PIL import Image
            self._pillow = Image
        except ImportError:
            pass
        try:
            import pytesseract
            self._tesseract = pytesseract
        except ImportError:
            pass

    def _download_file(self, url, dest_path):
        """Download a file with retry and size checking."""
        try:
            resp = requests.get(url, stream=True, timeout=30)
            resp.raise_for_status()

            # Check Content-Length header if available
            content_length = int(resp.headers.get('Content-Length', 0))
            if content_length > MAX_FILE_SIZE:
                logger.warning(f"File too large: {content_length} bytes (max {MAX_FILE_SIZE})")
                return False, "File exceeds size limit"

            with open(dest_path, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
            return True, None
        except requests.exceptions.RequestException as e:
            logger.error(f"Download failed: {e}")
            return False, str(e)

    def _detect_file_type(self, path, content_type=None):
        """Detect file type from extension and/or MIME type."""
        ext = os.path.splitext(path)[1].lower()
        if ext in TEXT_EXTS:
            return 'text'
        elif ext in IMAGE_EXTS:
            return 'image'
        elif ext in PDF_EXTS:
            return 'pdf'
        elif ext in SPREADSHEET_EXTS:
            return 'spreadsheet'
        elif ext in ARCHIVE_EXTS:
            return 'archive'
        # Fallback: try to detect from content
        try:
            with open(path, 'rb') as f:
                header = f.read(512)
            if header.startswith(b'PK'):  # ZIP/DOCX/XLSX/PPTX
                return 'archive' if ext == '' else 'binary'
            elif header.startswith(b'%PDF'):
                return 'pdf'
            elif header.startswith(b'{') or header.startswith(b'['):
                return 'text'
            elif header.startswith(b'<?xml') or header.startswith(b'<'):
                return 'text'
        except Exception:
            pass
        if content_type:
            if 'json' in content_type:
                return 'text'
            elif 'xml' in content_type:
                return 'text'
            elif 'html' in content_type:
                return 'text'
        return 'unknown'

    def _parse_text(self, path):
        """Read text files."""
        try:
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read(MAX_FILE_SIZE)
            return content
        except Exception as e:
            logger.error(f"Failed to read text file {path}: {e}")
            return None

    def _parse_pdf(self, path):
        """Extract text from PDF files."""
        if self._pdfplumber:
            try:
                text = []
                with self._pdfplumber.open(path) as pdf:
                    for i, page in enumerate(pdf.pages):
                        page_text = page.extract_text()
                        if page_text:
                            text.append(f"--- Page {i+1} ---\n{page_text}")
                return '\n\n'.join(text) if text else None
            except Exception as e:
                logger.error(f"PDF extraction failed with pdfplumber: {e}")

        if self._pdfium:
            try:
                doc = self._pdfium.Document(path)
                text = []
                for i, page in enumerate(doc):
                    page_text = page.get_textpage().get_text_bounded()
                    if page_text:
                        text.append(f"--- Page {i+1} ---\n{page_text}")
                return '\n\n'.join(text) if text else None
            except Exception as e:
                logger.error(f"PDF extraction failed with pypdfium2: {e}")
                return None

        # Fallback to pypdf
        try:
            from pypdf import PdfReader
            reader = PdfReader(path)
            text = []
            for i, page in enumerate(reader.pages):
                page_text = page.extract_text()
                if page_text:
                    text.append(f"--- Page {i+1} ---\n{page_text}")
            return '\n\n'.join(text) if text else None
        except Exception as e:
            logger.error(f"PDF extraction failed: {e}")
            return None

    def _parse_image(self, path):
        """Extract text from images using OCR."""
        if self._tesseract:
            try:
                return self._tesseract.image_to_string(self._pillow.open(path))
            except Exception as e:
                logger.error(f"OCR failed: {e}")
        return None

    def _parse_csv(self, path):
        """Parse CSV files into readable format."""
        try:
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read(MAX_FILE_SIZE)
            # Parse and reformat to show structure clearly
            reader = csv.DictReader(io.StringIO(content))
            if not reader.fieldnames:
                return content
            lines = []
            lines.append(','.join(reader.fieldnames))
            for i, row in enumerate(reader):
                line = ','.join(str(row.get(f, '')) for f in reader.fieldnames)
                lines.append(line)
                if i >= 100:  # Limit to 100 rows
                    lines.append('... [truncated]')
                    break
            return '\n'.join(lines)
        except Exception as e:
            logger.error(f"CSV parsing failed: {e}")
            return content if 'content' in dir() else None

    def _parse_excel(self, path):
        """Parse Excel files."""
        if self._pillow is None:
            return None
        try:
            import openpyxl
            wb = openpyxl.load_workbook(path, read_only=True)
            text_parts = []
            for sheet_name in wb.sheetnames[:3]:  # Limit to 3 sheets
                ws = wb[sheet_name]
                text_parts.append(f"--- Sheet: {sheet_name} ---")
                for i, row in enumerate(ws.iter_rows(values_only=True)):
                    if i >= 100:
                        text_parts.append('... [truncated]')
                        break
                    text_parts.append('\t'.join(str(c) if c is not None else '' for c in row))
            return '\n'.join(text_parts)
        except Exception as e:
            logger.error(f"Excel parsing failed: {e}")
            return None

    def _parse_archive(self, path):
        """Extract contents from archives (list files)."""
        import zipfile
        import tarfile
        import gzip as gz
        text_parts = []
        try:
            if path.endswith('.zip'):
                with zipfile.ZipFile(path, 'r') as z:
                    text_parts.extend(z.namelist())
            elif path.endswith(('.tar', '.gz', '.bz2', '.xz')):
                with tarfile.open(path, 'r:*') as t:
                    text_parts.extend(t.getnames())
            elif path.endswith('.gz'):
                try:
                    with gz.open(path, 'rt', encoding='utf-8', errors='replace') as f:
                        text_parts.append(f.read(MAX_FILE_SIZE))
                except Exception:
                    pass
            return '\n'.join(text_parts) if text_parts else None
        except Exception as e:
            logger.error(f"Archive extraction failed: {e}")
            return None

    def parse_file(self, path):
        """Parse a file and return extracted text content."""
        file_type = self._detect_file_type(path)
        logger.info(f"Parsing {os.path.basename(path)} as {file_type}")

        handlers = {
            'text': self._parse_text,
            'pdf': self._parse_pdf,
            'image': self._parse_image,
            'spreadsheet': self._parse_csv if path.endswith('.csv') else self._parse_excel,
            'archive': self._parse_archive,
        }

        handler = handlers.get(file_type)
        if handler:
            content = handler(path)
            if content:
                return content
            return f"*[Unable to extract content from {file_type} file]*"
        elif file_type == 'binary':
            return f"*[Binary file: {os.path.basename(path)} - cannot extract text]*"
        else:
            return f"*[Unsupported file type for: {os.path.basename(path)}]*"

    def process_message_urls(self, content):
        """
        Process a message content string: find Zulip upload URLs,
        download the files, extract content, and replace URLs with content.
        """
        # Find all Zulip upload URLs
        url_pattern = re.compile(r'https?://[^\s]+/user_uploads/[^\s\)]+')
        urls = url_pattern.findall(content)

        if not urls:
            return content, []

        processed_urls = []
        download_dir = os.path.join(os.path.expanduser('~'), '.cache', 'zulip_ai_uploads')
        os.makedirs(download_dir, exist_ok=True)

        for url in urls:
            # Extract filename from URL
            decoded_url = unquote(url)
            filename = os.path.basename(decoded_url.split('?')[0])
            if not filename:
                filename = 'downloaded_file'

            dest_path = os.path.join(download_dir, filename)

            # Download the file
            success, error = self._download_file(decoded_url, dest_path)
            if not success:
                content = content.replace(url, f"*⚠️ Failed to download: {error}*")
                continue

            # Parse the file content
            extracted = self.parse_file(dest_path)

            # Replace URL with extracted content or placeholder
            if extracted and not extracted.startswith('*['):
                # Format the content nicely
                content = content.replace(url, f"```[File: {filename}]\n{extracted}\n```")
            else:
                # File couldn't be parsed
                content = content.replace(url, f"*⚠️ Cannot extract text from {filename}*\n*Note: File saved at {dest_path}*")

            processed_urls.append(filename)
            logger.info(f"Processed: {filename}")

        return content, processed_urls


def clean_response(text):
    # Strip all ANSI escape codes (colour, dim, bold, cursor movement, etc.)
    ansi_escape = re.compile(r'\x1b(?:\[[0-9;]*[a-zA-Z]|\]\d*;[^\x07]*\x07|[@-Z\\-_])')
    clean = ansi_escape.sub('', text)
    # Replace long unicode horizontal lines with standard markdown horizontal rules
    clean = clean.replace("────────────────────────────────────────────", "---")
    # Collapse runs of blank lines to at most two
    clean = re.sub(r'\n{3,}', '\n\n', clean)
    return clean.strip()


class ZulipAiBridge:
    def __init__(self):
        if zulip is None:
            raise RuntimeError("The 'zulip' Python package is required. Install it via `pip install zulip`.")
        # Automatically loads credentials from ~/.zuliprc
        self.client = zulip.Client()
        self.bot_email = self.client.email
        print(f"Loaded credentials for: {self.bot_email} on {self.client.base_url}")
        self.detected_owner = self._detect_owner()
        # Initialize the file parser for processing uploaded documents
        self._file_parser = FileParser(self.client, self.client.base_url)
        print("File parser initialized — will extract content from uploaded documents.")

    def _detect_owner(self):
        """Try to detect the owner's Zulip email/username from past private messages."""
        try:
            payload = {
                "anchor": "newest",
                "num_before": 20,
                "num_after": 0,
                "narrow": [{"operator": "is", "operand": "private"}],
                "apply_markdown": False
            }
            res = self.client.get_messages(payload)
            if res.get("result") == "success":
                messages = res.get("messages", [])
                for msg in reversed(messages):
                    sender_email = msg.get("sender_email")
                    if sender_email and sender_email != self.bot_email:
                        print(f"Detected owner from past private messages: {sender_email}")
                        return sender_email
        except Exception as e:
            print(f"Error detecting owner from messages: {e}")
        return None

    def _send_reply(self, msg, content):
        """Send a reply to the same stream/topic or private thread."""
        if msg['type'] == 'private':
            self.client.send_message({
                "type": "private",
                "to": [msg['sender_email']],
                "content": content
            })
        else:
            self.client.send_message({
                "type": "stream",
                "to": msg['display_recipient'],
                "topic": msg['subject'],
                "content": content
            })

    def _get_context_messages(self, msg, limit=5):
        """Fetch up to `limit` previous messages in the same thread/conversation."""
        try:
            if msg['type'] == 'stream':
                narrow = [
                    {"operator": "stream", "operand": msg['display_recipient']},
                    {"operator": "topic", "operand": msg['subject']}
                ]
            else:  # private
                if isinstance(msg['display_recipient'], list):
                    emails = [r['email'] for r in msg['display_recipient']]
                    narrow = [{"operator": "pm-with", "operand": ",".join(emails)}]
                else:
                    narrow = [{"operator": "pm-with", "operand": msg['sender_email']}]

            payload = {
                "anchor": msg['id'],
                "num_before": limit,
                "num_after": 0,
                "narrow": narrow,
                "apply_markdown": False
            }
            res = self.client.get_messages(payload)
            if res.get("result") == "success":
                messages = res.get("messages", [])
                context_messages = [m for m in messages if m['id'] != msg['id']]
                return context_messages
            else:
                print(f"Zulip API error fetching context: {res.get('msg')}")
        except Exception as e:
            print(f"Error fetching context messages: {e}")
        return []

    def _construct_prompt_with_context(self, msg, content, context_messages):
        """Build a prompt that includes the context messages organically."""
        if not context_messages:
            return content

        context_window = ContextWindowManager()
        context_text = context_window.format_context(context_messages, self.bot_email)
        prompt = f"{context_text}\n\n{content}"

        return prompt

    def _process_message(self, msg, content):
        """Run the ai agent and send the result back. Runs in a background thread."""
        tid = threading.get_ident()
        print(f"[thread-{tid}] Processing: {content[:80]}")

        # Fetch context messages and manage context window
        context_messages = self._get_context_messages(msg)
        context_messages = self._manage_context_window(context_messages, content)
        prompt = self._construct_prompt_with_context(msg, content, context_messages)

        # Build a clean environment for the subprocess.
        # Inherit everything from the bridge's own env (which has the full PATH
        # and conda setup from the service file / interactive launch), then force
        # INFER_AUTO_APPROVE so execute_command never pauses for Y/n confirmation.
        run_env = os.environ.copy()
        run_env["INFER_AUTO_APPROVE"] = "1"
        # NOTE: Do NOT set INFER_RAW_OUTPUT here — when ai runs with a pipe
        # (non-TTY stdout) it already outputs only the final clean response.
        # INFER_RAW_OUTPUT caused streaming intermediate chunks to also be
        # printed, polluting the output captured by this bridge.
        # Let set_reminder default the reminder recipient back to whoever asked,
        # so "remind me tomorrow to ..." works with no email needed.
        sender_email = msg.get("sender_email")
        if sender_email:
            run_env["AI_REMINDER_ZULIP_TO"] = sender_email

        # Run the local `ai` CLI in quiet + auto-approve mode.
        # -q suppresses the think tool reasoning output.
        # -y auto-approves command execution.
        # Streaming intermediate content is suppressed automatically because
        # stdout is a pipe (non-TTY), so only the final answer is captured.
        # With schedule_task properly used, the agent returns immediately for timed work.
        try:
            result = subprocess.run(
                ["ai", "-y", "-q", prompt],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=run_env,
                text=True,
                timeout=600
            )
            response_text = result.stdout
            if result.returncode != 0 and result.stderr:
                response_text += f"\n\n*Stderr:*\n```\n{result.stderr}\n```"

            response_text = clean_response(response_text)
            if not response_text:
                response_text = "*(agent returned no output)*"

        except subprocess.TimeoutExpired:
            response_text = (
                "⏱️ The agent timed out after 10 minutes. "
                "For long-running tasks, ask me to **schedule** them so they run in "
                "the background and notify you when done."
            )
        except Exception as e:
            response_text = f"⚠️ Failed to run local `ai` CLI: {str(e)}"

        self._send_reply(msg, response_text)
        print(f"[thread-{tid}] Done.")

    def handle_message(self, msg):
        """Handle a Zulip message."""
        sender_email = msg['sender_email']

        # Don't respond to our own messages
        if sender_email == self.bot_email:
            return

        # Restrict to the owner/configured user to protect privacy
        import getpass
        allowed_user = os.environ.get("ZULIP_USER") or self.detected_owner or getpass.getuser()

        # Normalize values for case-insensitive comparison
        allowed_user_clean = allowed_user.strip().lower()
        sender_email_clean = sender_email.strip().lower()
        sender_full_name_clean = msg.get('sender_full_name', '').strip().lower()
        sender_username_clean = sender_email_clean.split('@')[0] if '@' in sender_email_clean else sender_email_clean

        is_allowed = (
            allowed_user_clean == sender_email_clean or
            allowed_user_clean == sender_full_name_clean or
            allowed_user_clean == sender_username_clean or
            (allowed_user_clean.isdigit() and sender_username_clean == f"user{allowed_user_clean}") or
            (sender_username_clean.startswith("user") and sender_username_clean[4:] == allowed_user_clean)
        )

        print(f"Privacy validation - allowed: '{allowed_user_clean}', sender: '{sender_email_clean}', name: '{sender_full_name_clean}', user: '{sender_username_clean}' -> ALLOWED={is_allowed}")

        if not is_allowed:
            # Quietly ignore messages from others to protect privacy
            return

        content = msg['content'].strip()

        # If the bot is mentioned in a stream, strip the mention syntax (e.g. @**AI Bot**)
        if msg['type'] != 'private' and content.startswith('@**'):
            mention_end = content.find('**')
            if mention_end != -1:
                mention_end_close = content.find('**', mention_end + 2)
                if mention_end_close != -1:
                    content = content[mention_end_close + 2:].strip()

        # Replace Zulip upload URLs with extracted file content
        content, processed = self._file_parser.process_message_urls(content)

        print(f"Received query from {sender_email}: {content}")

        # Dispatch to a daemon thread — the Zulip event loop returns immediately
        # and is ready to receive the next message while this one is being processed.
        t = threading.Thread(
            target=self._process_message,
            args=(msg, content),
            daemon=True
        )
        t.start()

    def _manage_context_window(self, context_messages, current_content):
        """Apply context window management to prevent overflow."""
        window_manager = ContextWindowManager()
        truncated_messages, truncated = window_manager.truncate_context(
            context_messages, current_content
        )
        if truncated:
            logger.info(f"Context truncated from {len(context_messages)} to {len(truncated_messages)} messages")
        return truncated_messages

    def run(self):
        """Start the Zulip AI Bridge with automatic reconnection."""
        print("🚀 Starting Zulip AI Bridge listener (threaded — concurrent messages supported)...")
        
        max_backoff = 60  # Maximum backoff in seconds
        current_backoff = 1
        
        while True:
            try:
                self.client.call_on_each_message(self.handle_message)
            except (ConnectionError, requests.exceptions.RequestException) as e:
                print(f"⚠️ Connection error: {e}")
                print(f"⏳ Reconnecting in {current_backoff}s...")
                import time
                time.sleep(current_backoff)
                current_backoff = min(current_backoff * 2, max_backoff)
            except Exception as e:
                print(f"❌ Unexpected error: {e}")
                print(f"⏳ Restarting in {current_backoff}s...")
                import time
                time.sleep(current_backoff)
                current_backoff = min(current_backoff * 2, max_backoff)


class ContextWindowManager:
    """Manages conversation context to stay within AI model's context window."""
    
    def __init__(self, max_tokens=4096, max_messages=10):
        """
        Initialize the context window manager.
        
        Args:
            max_tokens: Maximum estimated tokens for the prompt (conservative estimate)
            max_messages: Maximum number of context messages to include
        """
        self.max_tokens = max_tokens
        self.max_messages = max_messages
    
    def estimate_tokens(self, text):
        """Rough token estimation: ~4 chars per token for English text."""
        return len(text) // 4
    
    def truncate_context(self, context_messages, current_content, max_context_length=3000):
        """
        Truncate context messages to fit within token budget.
        
        Args:
            context_messages: List of message dictionaries
            current_content: The current message content
            max_context_length: Maximum length for context in characters
            
        Returns:
            Tuple of (truncated messages, whether truncation occurred)
        """
        if not context_messages:
            return context_messages, False
        
        # Estimate total prompt length
        estimated_prompt_length = len(current_content)
        
        # Build context incrementally until we hit the limit
        truncated = []
        for msg in context_messages:
            sender = msg.get("sender_full_name", msg.get("sender_email", "Unknown"))
            body = msg.get("content", "").strip()
            msg_length = len(f"- {sender}: {body}")
            
            if estimated_prompt_length + msg_length > max_context_length:
                logger.warning(
                    f"Context truncated: {len(context_messages)} messages would exceed "
                    f"token budget. Keeping {len(truncated)} messages."
                )
                return truncated, True
            
            truncated.append(msg)
            estimated_prompt_length += msg_length
        
        return truncated, False
    
    def format_context(self, context_messages, bot_email):
        """Format context messages into a readable string for the prompt."""
        if not context_messages:
            return ""
        
        context_lines = ["---", "Recent conversation context (for reference):"]
        for m in context_messages:
            sender = m.get("sender_full_name", m.get("sender_email"))
            if m.get("sender_email") == bot_email:
                sender = "AI (You)"
            else:
                sender = f"User ({sender})"
            body = m.get("content", "").strip()
            if "\n" in body:
                body = "\n".join("  " + line for line in body.splitlines())
            context_lines.append(f"- {sender}: {body}")
        context_lines.append("---")
        context_lines.append("Latest query/message:")
        
        return "\n".join(context_lines)

if __name__ == "__main__":
    try:
        bridge = ZulipAiBridge()
        bridge.run()
    except Exception as e:
        print(f"Error starting bridge: {e}")
        sys.exit(1)
