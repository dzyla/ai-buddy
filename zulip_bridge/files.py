"""Zulip upload download + text extraction (FileParser)."""
import os
import re
import csv
import io
import time
import shutil
import logging
import tempfile
import requests
from urllib.parse import unquote


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

# Zulip rejects/truncates messages over 10000 chars; leave headroom for the

# Per-message upload directories older than this are pruned.
UPLOAD_RETENTION_S = 7 * 24 * 3600


def _safe_upload_name(name):
    """Reduce an attacker-controlled upload label to a harmless basename."""
    name = os.path.basename((name or "").replace("\\", "/")).strip()
    name = re.sub(r'[\x00-\x1f]', '', name)
    if name in ("", ".", ".."):
        name = "downloaded_file"
    return name[:200]


class FileParser:
    """
    Downloads Zulip uploads and extracts text content for various file types.
    """

    def __init__(self, client, base_url):
        self.client = client
        # Normalize base_url to root origin (e.g. strip trailing /api or /api/v1)
        if isinstance(base_url, str):
            cleaned_base = re.sub(r'/api(?:/v1)?/?$', '', base_url).rstrip('/')
        else:
            cleaned_base = ""
        self.base_url = cleaned_base
        # Try to import optional dependencies
        self._pdfplumber = None
        self._pdfium = None
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
        # If relative URL, make absolute with root origin
        if url.startswith('/'):
            try:
                from urllib.parse import urlparse
                pb = urlparse(self.base_url)
                origin = f"{pb.scheme}://{pb.netloc}" if pb.netloc else self.base_url.rstrip('/')
            except Exception:
                origin = self.base_url.rstrip('/')
            full_url = f"{origin}{url}"
        else:
            full_url = url

        # Validate URL is from a trusted domain (the configured Zulip server)
        if not self._is_trusted_url(full_url):
            logger.warning(f"Blocked download from untrusted domain: {full_url}")
            return False, "Download from untrusted domain"

        resp = None
        try:
            # Use authenticated Zulip session when available (crucial for private Zulip uploads)
            session = getattr(self.client, "session", None)
            if isinstance(session, requests.Session):
                resp = session.get(full_url, stream=True, timeout=30)
            else:
                resp = requests.get(full_url, stream=True, timeout=30)

            # The final URL after following redirects must also be trusted.
            if not self._is_trusted_redirect(resp.url, full_url):
                logger.warning(f"Redirect to untrusted domain: {resp.url}")
                return False, "Download redirects to untrusted domain"
            resp.raise_for_status()

            # Check Content-Length header if available
            try:
                content_length = int(resp.headers.get('Content-Length', 0) or 0)
            except (TypeError, ValueError):
                content_length = 0
            if content_length > MAX_FILE_SIZE:
                logger.warning(f"File too large: {content_length} bytes (max {MAX_FILE_SIZE})")
                return False, "File exceeds size limit"

            # Content-Length may be absent or lie: enforce the cap while streaming.
            written = 0
            with open(dest_path, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    written += len(chunk)
                    if written > MAX_FILE_SIZE:
                        break
                    f.write(chunk)
            if written > MAX_FILE_SIZE:
                logger.warning(f"File exceeded {MAX_FILE_SIZE} bytes while streaming; aborted")
                try:
                    os.remove(dest_path)
                except OSError:
                    pass
                return False, "File exceeds size limit"
            return True, None
        except requests.exceptions.RequestException as e:
            logger.error(f"Download failed: {e}")
            return False, str(e)
        except TimeoutError as e:
            logger.error(f"Download timed out: {e}")
            return False, str(e)
        except OSError as e:
            logger.error(f"Download file system error: {e}")
            return False, str(e)
        finally:
            if resp is not None and hasattr(resp, "close"):
                try:
                    resp.close()
                except Exception:
                    pass

    def _is_trusted_url(self, url):
        """Check if a URL is from the configured Zulip server domain.

        Accepts exact domain matches, subdomains of the allowed domain,
        and relative paths (which are always same-origin).
        """
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            # Relative paths (empty netloc) are same-origin and always trusted.
            if not parsed.netloc:
                return True
            # Compare hostnames (drops userinfo and port). Only the configured
            # host and its subdomains are trusted -- never a parent domain,
            # since the authenticated session would send credentials there.
            base_parsed = urlparse(self.base_url if '://' in self.base_url else f"https://{self.base_url}")
            allowed = (base_parsed.hostname or '').lower().rstrip('.')
            downloaded = (parsed.hostname or '').lower().rstrip('.')
            if not allowed or not downloaded:
                return False
            return downloaded == allowed or downloaded.endswith("." + allowed)
        except Exception:
            return False

    def _is_trusted_redirect(self, final_url, original_url):
        """Check if a redirect target is trusted.

        After following redirects, the final URL must also be from a
        trusted domain to prevent redirects to malicious sites.
        """
        try:
            if not isinstance(final_url, str):
                return True
            from urllib.parse import urlparse
            final_parsed = urlparse(final_url)
            # If no netloc, treat as same-origin relative redirect.
            if not final_parsed.netloc:
                return True
            return self._is_trusted_url(final_url)
        except Exception:
            return False

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
        Supports both absolute and relative Zulip uploads as well as markdown link syntax.
        """
        # Match both markdown links [filename](.../user_uploads/...) and bare URLs
        pattern = re.compile(
            r'\[([^\]]*)\]\(((?:https?://[^\s\)]+)?/user_uploads/[^\s\)]+)\)|'
            r'(https?://[^\s\)]+/user_uploads/[^\s\)]+)|'
            r'(?<!\()(/user_uploads/[^\s\)]+)'
        )
        matches = list(pattern.finditer(content))
        if not matches:
            return content, []

        processed_urls = []
        upload_root = os.path.join(os.path.expanduser('~'), '.cache', 'zulip_ai_uploads')
        os.makedirs(upload_root, exist_ok=True)
        self._prune_uploads(upload_root)
        # One private directory per message: concurrent messages attaching the
        # same filename can no longer clobber each other's downloads.
        download_dir = tempfile.mkdtemp(prefix="msg_", dir=upload_root)

        for m in matches:
            full_match = m.group(0)
            if m.group(2):
                label = m.group(1)
                raw_url = m.group(2)
            elif m.group(3):
                label = ""
                raw_url = m.group(3)
            else:
                label = ""
                raw_url = m.group(4)

            # Ensure URL is absolute for download
            if raw_url.startswith('/'):
                download_url = f"{self.base_url}{raw_url}"
            else:
                download_url = raw_url

            decoded_url = unquote(raw_url)
            filename = label.strip() if label.strip() else os.path.basename(decoded_url.split('?')[0])
            if not filename or filename == 'user_uploads':
                filename = os.path.basename(decoded_url.split('?')[0]) or 'downloaded_file'
            # The label is user-controlled: never let it escape download_dir.
            filename = _safe_upload_name(filename)

            dest_path = os.path.join(download_dir, filename)

            # Download the file
            success, error = self._download_file(download_url, dest_path)
            if not success:
                content = content.replace(full_match, f"*⚠️ Failed to download: {error}*")
                continue

            # Parse the file content
            extracted = self.parse_file(dest_path)

            # Replace match with extracted content or placeholder
            if extracted and not extracted.startswith('*['):
                # Cap extracted text to 50k chars so we don't overwhelm the prompt
                if len(extracted) > 50000:
                    extracted = extracted[:50000] + f"\n... [truncated. File was {len(extracted)} characters.]"
                content = content.replace(full_match, f"```[File: {filename}]\n{extracted}\n```")
            else:
                content = content.replace(full_match, f"*⚠️ Cannot extract text from {filename}*\n*Note: File saved at {dest_path}*")

            processed_urls.append(filename)
            logger.info(f"Processed: {filename}")

        return content, processed_urls

    @staticmethod
    def _prune_uploads(upload_root, max_age=UPLOAD_RETENTION_S):
        """Delete per-message upload directories older than `max_age` seconds."""
        cutoff = time.time() - max_age
        try:
            for entry in os.scandir(upload_root):
                try:
                    if entry.stat(follow_symlinks=False).st_mtime < cutoff:
                        if entry.is_dir(follow_symlinks=False):
                            shutil.rmtree(entry.path, ignore_errors=True)
                        else:
                            os.remove(entry.path)
                except OSError:
                    pass
        except OSError:
            pass
