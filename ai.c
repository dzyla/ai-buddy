#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include <unistd.h>
#include <stdint.h>
#include <curl/curl.h>
#include "jsmn.h"

#include <strings.h>

#define MAX_LINE 1024
#define MAX_VAL  512

// Config globals
static char api_url[MAX_VAL];
static char api_key[MAX_VAL];
static char model[MAX_VAL];

static const char *SYSTEM_PROMPT = 
    "You are a CLI tool with tool-calling capabilities. Output your final response using clean, simple markdown. "
    "Use bold text, bullet points, headers, or code blocks where it improves readability. Keep the output concise. "
    "If you search the web and the search snippets do not contain the answer, you MUST use the fetch_webpage tool to visit the relevant URLs and read their content to find the answer. "
    "Always answer the user's question directly using the retrieved data. Never tell the user to check a website or search themselves.";


/* ---------------- HELPERS ---------------- */

// Minimal JSON string escaper (handles ", \, and newline)
// Returns a new allocated string you must free.
static char* json_escape(const char *src) {
    if (!src) return calloc(1, 1);
    char *dest = malloc(strlen(src) * 6 + 1);
    char *p = dest;
    while (*src) {
        unsigned char c = *src;
        if (c == '"') { *p++ = '\\'; *p++ = '"'; }
        else if (c == '\\') { *p++ = '\\'; *p++ = '\\'; }
        else if (c == '\n') { *p++ = '\\'; *p++ = 'n'; }
        else if (c == '\r') { *p++ = '\\'; *p++ = 'r'; }
        else if (c == '\t') { *p++ = '\\'; *p++ = 't'; }
        else if (c == '\b') { *p++ = '\\'; *p++ = 'b'; }
        else if (c == '\f') { *p++ = '\\'; *p++ = 'f'; }
        else if (c < 0x20) {
            sprintf(p, "\\u%04x", c);
            p += 6;
        } else {
            *p++ = *src;
        }
        src++;
    }
    *p = 0;
    return dest;
}

// Reads stdin into a dynamically allocated string
static char* read_stdin() {
    if (isatty(fileno(stdin))) return NULL; // No pipe detected

    size_t size = 4096, len = 0;
    char *buf = malloc(size);
    int c;
    while ((c = getchar()) != EOF) {
        buf[len++] = c;
        if (len >= size - 1) {
            size *= 2;
            buf = realloc(buf, size);
        }
    }
    buf[len] = 0;
    return buf;
}

static int hexval(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return 10 + (c - 'a');
    if (c >= 'A' && c <= 'F') return 10 + (c - 'A');
    return -1;
}

static void emit_utf8(uint32_t cp) {
    if (cp <= 0x7F) {
        fputc((int)cp, stdout);
    } else if (cp <= 0x7FF) {
        fputc(0xC0 | ((cp >> 6) & 0x1F), stdout);
        fputc(0x80 | (cp & 0x3F), stdout);
    } else if (cp <= 0xFFFF) {
        fputc(0xE0 | ((cp >> 12) & 0x0F), stdout);
        fputc(0x80 | ((cp >> 6) & 0x3F), stdout);
        fputc(0x80 | (cp & 0x3F), stdout);
    } else if (cp <= 0x10FFFF) {
        fputc(0xF0 | ((cp >> 18) & 0x07), stdout);
        fputc(0x80 | ((cp >> 12) & 0x3F), stdout);
        fputc(0x80 | ((cp >> 6) & 0x3F), stdout);
        fputc(0x80 | (cp & 0x3F), stdout);
    } else {
        fputc('?', stdout);
    }
}

static void print_json_string_unescaped(const char *s, int len) {
    int i = 0;
    while (i < len) {
        char c = s[i++];
        if (c != '\\') {
            fputc(c, stdout);
            continue;
        }
        if (i >= len) { fputc('\\', stdout); break; }
        char esc = s[i++];
        switch (esc) {
            case 'n': fputc('\n', stdout); break;
            case 'r': fputc('\r', stdout); break;
            case 't': fputc('\t', stdout); break;
            case 'b': fputc('\b', stdout); break;
            case 'f': fputc('\f', stdout); break;
            case '"': fputc('"', stdout); break;
            case '\\': fputc('\\', stdout); break;
            case '/': fputc('/', stdout); break;
            case 'u': {
                if (i + 4 > len) { fputc('?', stdout); break; }
                uint32_t cp = 0;
                for (int k = 0; k < 4; k++) {
                    int hv = hexval(s[i + k]);
                    if (hv < 0) { cp = 0xFFFD; break; }
                    cp = (cp << 4) | (uint32_t)hv;
                }
                i += 4;

                if (cp >= 0xD800 && cp <= 0xDBFF) {
                    if (i + 6 <= len && s[i] == '\\' && s[i + 1] == 'u') {
                        uint32_t low = 0;
                        int ok = 1;
                        for (int k = 0; k < 4; k++) {
                            int hv = hexval(s[i + 2 + k]);
                            if (hv < 0) { ok = 0; break; }
                            low = (low << 4) | (uint32_t)hv;
                        }
                        if (ok && low >= 0xDC00 && low <= 0xDFFF) {
                            i += 6;
                            cp = 0x10000 + ((cp - 0xD800) << 10) + (low - 0xDC00);
                        }
                    }
                }
                emit_utf8(cp);
                break;
            }
            default:
                fputc(esc, stdout);
                break;
        }
    }
}

/* ---------------- YAML PARSER (REMOVED) ---------------- */

/* ---------------- HTTP RESPONSE HANDLING ---------------- */

struct response {
    char *data;
    size_t size;
};

static size_t write_cb(void *ptr, size_t size, size_t nmemb, void *userdata) {
    size_t realsize = size * nmemb;
    struct response *mem = (struct response *)userdata;
    
    char *ptr_realloc = realloc(mem->data, mem->size + realsize + 1);
    if(!ptr_realloc) return 0; // Out of memory

    mem->data = ptr_realloc;
    memcpy(&(mem->data[mem->size]), ptr, realsize);
    mem->size += realsize;
    mem->data[mem->size] = 0;
    return realsize;
}

/* ---------------- MAIN ---------------- */
static char* unescape_json_string(const char *s, int len) {
    char *dest = malloc(len + 1);
    int i = 0;
    int d = 0;
    while (i < len) {
        char c = s[i++];
        if (c != '\\') {
            dest[d++] = c;
            continue;
        }
        if (i >= len) { dest[d++] = '\\'; break; }
        char esc = s[i++];
        switch (esc) {
            case 'n': dest[d++] = '\n'; break;
            case 'r': dest[d++] = '\r'; break;
            case 't': dest[d++] = '\t'; break;
            case 'b': dest[d++] = '\b'; break;
            case 'f': dest[d++] = '\f'; break;
            case '"': dest[d++] = '"'; break;
            case '\\': dest[d++] = '\\'; break;
            case '/': dest[d++] = '/'; break;
            case 'u': {
                if (i + 4 <= len) {
                    uint32_t cp = 0;
                    for (int k = 0; k < 4; k++) {
                        int hv = hexval(s[i + k]);
                        if (hv < 0) { cp = 0; break; }
                        cp = (cp << 4) | (uint32_t)hv;
                    }
                    i += 4;
                    if (cp > 0 && cp <= 0x7F) {
                        dest[d++] = (char)cp;
                    } else {
                        dest[d++] = '\\';
                        dest[d++] = 'u';
                        for (int k = 0; k < 4; k++) dest[d++] = s[i - 4 + k];
                    }
                } else {
                    dest[d++] = '\\';
                    dest[d++] = 'u';
                }
                break;
            }
            default:
                dest[d++] = esc;
                break;
        }
    }
    dest[d] = '\0';
    return dest;
}

static char* shell_escape(const char *src) {
    size_t len = strlen(src);
    char *dest = malloc(len * 4 + 3);
    char *p = dest;
    *p++ = '\'';
    while (*src) {
        if (*src == '\'') {
            strcpy(p, "'\\''");
            p += 4;
        } else {
            *p++ = *src;
        }
        src++;
    }
    *p++ = '\'';
    *p = '\0';
    return dest;
}

static char* run_shell_command(const char *cmd) {
    FILE *fp = popen(cmd, "r");
    if (!fp) {
        return strdup("Error: failed to run command");
    }
    
    size_t size = 4096;
    size_t len = 0;
    char *buf = malloc(size);
    if (!buf) {
        pclose(fp);
        return NULL;
    }
    buf[0] = '\0';
    
    char tmp[1024];
    while (fgets(tmp, sizeof(tmp), fp) != NULL) {
        size_t tmp_len = strlen(tmp);
        if (len + tmp_len >= size - 1) {
            size *= 2;
            char *new_buf = realloc(buf, size);
            if (!new_buf) {
                free(buf);
                pclose(fp);
                return NULL;
            }
            buf = new_buf;
        }
        strcpy(buf + len, tmp);
        len += tmp_len;
    }
    
    pclose(fp);
    return buf;
}

static int json_skip_token(jsmntok_t *tokens, int r, int start_idx) {
    if (start_idx >= r) return r;
    int end = tokens[start_idx - 1].end;
    int i = start_idx;
    while (i < r && tokens[i].start < end) {
        i++;
    }
    return i;
}

static char* append_message(char *messages_json, const char *msg_to_append) {
    size_t orig_len = strlen(messages_json);
    if (orig_len < 2) return messages_json;
    
    size_t append_len = strlen(msg_to_append);
    size_t new_size = orig_len + append_len + 5;
    char *new_buf = realloc(messages_json, new_size);
    if (!new_buf) return NULL;
    
    char *p = new_buf + orig_len - 1;
    while (p > new_buf && *p != ']') p--;
    
    if (p == new_buf) {
        free(new_buf);
        return NULL;
    }
    
    int has_elements = 0;
    char *q = new_buf + 1;
    while (q < p) {
        if (!isspace((unsigned char)*q)) {
            has_elements = 1;
            break;
        }
        q++;
    }
    
    if (has_elements) {
        *p = ',';
        p++;
    }
    
    strcpy(p, msg_to_append);
    strcat(p, "]");
    
    return new_buf;
}

static const char b64chars[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

static char* base64_encode(const unsigned char *data, size_t input_length) {
    size_t output_length = 4 * ((input_length + 2) / 3);
    char *encoded_data = malloc(output_length + 1);
    if (!encoded_data) return NULL;
    
    for (size_t i = 0, j = 0; i < input_length;) {
        uint32_t octet_a = i < input_length ? data[i++] : 0;
        uint32_t octet_b = i < input_length ? data[i++] : 0;
        uint32_t octet_c = i < input_length ? data[i++] : 0;
        
        uint32_t triple = (octet_a << 16) + (octet_b << 8) + octet_c;
        
        encoded_data[j++] = b64chars[(triple >> 18) & 0x3F];
        encoded_data[j++] = b64chars[(triple >> 12) & 0x3F];
        encoded_data[j++] = i > input_length + 1 ? '=' : b64chars[(triple >> 6) & 0x3F];
        encoded_data[j++] = i > input_length ? '=' : b64chars[triple & 0x3F];
    }
    encoded_data[output_length] = '\0';
    return encoded_data;
}

static int is_image_file(const char *path) {
    const char *ext = strrchr(path, '.');
    if (!ext) return 0;
    if (strcasecmp(ext, ".png") == 0 ||
        strcasecmp(ext, ".jpg") == 0 ||
        strcasecmp(ext, ".jpeg") == 0 ||
        strcasecmp(ext, ".webp") == 0) {
        return access(path, F_OK) == 0;
    }
    return 0;
}

static char* read_image_base64(const char *path, const char **mime_type) {
    FILE *fp = fopen(path, "rb");
    if (!fp) return NULL;
    
    fseek(fp, 0, SEEK_END);
    long size = ftell(fp);
    fseek(fp, 0, SEEK_SET);
    
    if (size <= 0) {
        fclose(fp);
        return NULL;
    }
    
    unsigned char *buf = malloc(size);
    if (!buf) {
        fclose(fp);
        return NULL;
    }
    
    size_t read_bytes = fread(buf, 1, size, fp);
    fclose(fp);
    
    char *b64 = base64_encode(buf, read_bytes);
    free(buf);
    
    const char *ext = strrchr(path, '.');
    if (strcasecmp(ext, ".png") == 0) *mime_type = "image/png";
    else if (strcasecmp(ext, ".webp") == 0) *mime_type = "image/webp";
    else *mime_type = "image/jpeg";
    
    return b64;
}

static char* read_memory_file() {
    char *home = getenv("HOME");
    if (!home) return NULL;
    char path[1024];
    snprintf(path, sizeof(path), "%s/.config/ai/memory.txt", home);
    
    FILE *fp = fopen(path, "r");
    if (!fp) return NULL;
    
    fseek(fp, 0, SEEK_END);
    long size = ftell(fp);
    fseek(fp, 0, SEEK_SET);
    
    if (size <= 0) {
        fclose(fp);
        return NULL;
    }
    
    if (size > 4096) size = 4096;
    
    char *buf = malloc(size + 1);
    if (!buf) {
        fclose(fp);
        return NULL;
    }
    
    size_t read_bytes = fread(buf, 1, size, fp);
    buf[read_bytes] = '\0';
    fclose(fp);
    return buf;
}

int main(int argc, char **argv) {
    if (argc < 2) { fprintf(stderr, "Usage: %s \"prompt\"\n", argv[0]); return 1; }

    // Load from Environment Variables
    char *env_url = getenv("INFER_BASE_URL");
    char *env_key = getenv("INFER_API_KEY");
    char *env_model = getenv("INFER_MODEL");

    if (!env_url || !*env_url || !env_key || !*env_key || !env_model || !*env_model) {
        fprintf(stderr, "Error: missing required environment variables.\n");
        if (!env_url || !*env_url) fprintf(stderr, "Please set INFER_BASE_URL environment variable.\n");
        if (!env_key || !*env_key) fprintf(stderr, "Please set INFER_API_KEY environment variable.\n");
        if (!env_model || !*env_model) fprintf(stderr, "Please set INFER_MODEL environment variable.\n");
        return 1;
    }

    const char *path = "chat/completions";
    size_t base_len = strlen(env_url);
    int needs_slash = base_len > 0 && env_url[base_len - 1] != '/';
    snprintf(api_url, sizeof(api_url), "%s%s%s", env_url, needs_slash ? "/" : "", path);
    snprintf(api_key, sizeof(api_key), "%s", env_key);
    snprintf(model, sizeof(model), "%s", env_model);

    // Get tools JSON from python script
    const char *mcp_script = "./ai_mcp.py";
    if (access(mcp_script, F_OK) != 0) {
        mcp_script = "/usr/local/bin/ai_mcp.py";
    }
    
    char tools_cmd[1024];
    snprintf(tools_cmd, sizeof(tools_cmd), "python3 %s list-tools", mcp_script);
    char *tools_json = run_shell_command(tools_cmd);
    if (tools_json && (strncmp(tools_json, "Error", 5) == 0 || strlen(tools_json) < 5)) {
        free(tools_json);
        tools_json = NULL;
    }

    // 1. Prepare Inputs
    char *pipe_in = read_stdin();

    // Check if any argument is an image file
    char *image_path = NULL;
    for (int i = 1; i < argc; i++) {
        if (is_image_file(argv[i])) {
            image_path = argv[i];
            break;
        }
    }

    size_t prompt_len = 0;
    for (int i = 1; i < argc; i++) {
        if (image_path && strcmp(argv[i], image_path) == 0) continue;
        prompt_len += strlen(argv[i]) + 1;
    }
    
    char *prompt = malloc(prompt_len + 1);
    prompt[0] = '\0';
    int added = 0;
    for (int i = 1; i < argc; i++) {
        if (image_path && strcmp(argv[i], image_path) == 0) continue;
        if (added) strcat(prompt, " ");
        strcat(prompt, argv[i]);
        added = 1;
    }

    // Initialize messages JSON array
    char *messages_json = malloc(4096);
    strcpy(messages_json, "[]");

    // Add System Prompt & Memory Context
    char *memory = read_memory_file();
    char *safe_system = json_escape(SYSTEM_PROMPT);
    char *sys_msg = NULL;
    if (memory) {
        char *safe_mem = json_escape(memory);
        size_t mlen = strlen(safe_system) + strlen(safe_mem) + 256;
        sys_msg = malloc(mlen);
        sprintf(sys_msg, "{\"role\":\"system\",\"content\":\"%s\\n\\nPersistent Memory/Preferences:\\n%s\"}", safe_system, safe_mem);
        free(safe_mem);
        free(memory);
    } else {
        sys_msg = malloc(strlen(safe_system) + 128);
        sprintf(sys_msg, "{\"role\":\"system\",\"content\":\"%s\"}", safe_system);
    }
    messages_json = append_message(messages_json, sys_msg);
    free(safe_system); free(sys_msg);

    // Add User Prompt
    char *safe_prompt = json_escape(prompt);
    char *safe_pipe = pipe_in ? json_escape(pipe_in) : NULL;
    char *user_content = NULL;
    if (safe_pipe && *safe_pipe) {
        size_t len = strlen(safe_prompt) + strlen(safe_pipe) + 128;
        user_content = malloc(len);
        sprintf(user_content, "%s\\n\\nContext:\\n%s", safe_prompt, safe_pipe);
    } else {
        user_content = strdup(safe_prompt);
    }

    char *user_msg = NULL;
    if (image_path) {
        const char *mime_type = NULL;
        char *b64 = read_image_base64(image_path, &mime_type);
        if (b64) {
            size_t msg_len = strlen(user_content) + strlen(b64) + strlen(mime_type) + 512;
            user_msg = malloc(msg_len);
            sprintf(user_msg, "{\"role\":\"user\",\"content\":[{\"type\":\"text\",\"text\":\"%s\"},{\"type\":\"image_url\",\"image_url\":{\"url\":\"data:%s;base64,%s\"}}]}",
                    user_content, mime_type, b64);
            free(b64);
        }
    }

    if (!user_msg) {
        user_msg = malloc(strlen(user_content) + 128);
        sprintf(user_msg, "{\"role\":\"user\",\"content\":\"%s\"}", user_content);
    }

    messages_json = append_message(messages_json, user_msg);
    free(safe_prompt); if(safe_pipe) free(safe_pipe);
    free(user_content); free(user_msg);

    // 2. Setup Curl
    CURL *c = curl_easy_init();
    struct curl_slist *h = NULL;
    char auth[1024]; snprintf(auth, sizeof(auth), "Authorization: Bearer %s", api_key);
    h = curl_slist_append(h, "Content-Type: application/json");
    h = curl_slist_append(h, auth);

    curl_easy_setopt(c, CURLOPT_URL, api_url);
    curl_easy_setopt(c, CURLOPT_HTTPHEADER, h);
    curl_easy_setopt(c, CURLOPT_WRITEFUNCTION, write_cb);

    int debug_mode = getenv("INFER_DEBUG") != NULL;
    int loop_count = 0;
    int has_more = 1;

    while (has_more && loop_count < 20) {
        loop_count++;
        
        char *payload = NULL;
        size_t plen = strlen(model) + strlen(messages_json) + (tools_json ? strlen(tools_json) : 0) + 256;
        payload = malloc(plen);
        if (tools_json && strlen(tools_json) > 10) {
            sprintf(payload, "{\"model\":\"%s\",\"stream\":false,\"messages\":%s,\"tools\":%s}", model, messages_json, tools_json);
        } else {
            sprintf(payload, "{\"model\":\"%s\",\"stream\":false,\"messages\":%s}", model, messages_json);
        }

        if (debug_mode) {
            fprintf(stderr, "[debug] Loop %d payload: %s\n", loop_count, payload);
        }

        struct response chunk = {0};
        curl_easy_setopt(c, CURLOPT_POSTFIELDS, payload);
        curl_easy_setopt(c, CURLOPT_WRITEDATA, (void *)&chunk);

        CURLcode res = curl_easy_perform(c);

        if (res != CURLE_OK || !chunk.data) {
            fprintf(stderr, "Request failed: %s\n", curl_easy_strerror(res));
            free(payload);
            if (chunk.data) free(chunk.data);
            break;
        }

        if (debug_mode) {
            fprintf(stderr, "[debug] Loop %d response: %s\n", loop_count, chunk.data);
        }

        jsmn_parser p;
        jsmntok_t tok[2048];
        jsmn_init(&p);
        int r = jsmn_parse(&p, chunk.data, chunk.size, tok, 2048);

        if (r < 0) {
            fprintf(stderr, "Failed to parse JSON response: %d\n", r);
            free(payload);
            free(chunk.data);
            break;
        }

        int finish_reason_tok = -1;
        int message_tok = -1;
        int tool_calls_tok = -1;

        for (int i = 1; i < r; i++) {
            if (tok[i].type == JSMN_STRING) {
                int len = tok[i].end - tok[i].start;
                if (len == 13 && strncmp(chunk.data + tok[i].start, "finish_reason", 13) == 0) {
                    finish_reason_tok = i + 1;
                } else if (len == 7 && strncmp(chunk.data + tok[i].start, "message", 7) == 0) {
                    message_tok = i + 1;
                } else if (len == 10 && strncmp(chunk.data + tok[i].start, "tool_calls", 10) == 0) {
                    tool_calls_tok = i + 1;
                }
            }
        }

        if (message_tok != -1) {
            char *msg_str = malloc(tok[message_tok].end - tok[message_tok].start + 1);
            memcpy(msg_str, chunk.data + tok[message_tok].start, tok[message_tok].end - tok[message_tok].start);
            msg_str[tok[message_tok].end - tok[message_tok].start] = '\0';
            messages_json = append_message(messages_json, msg_str);
            free(msg_str);
        }

        int should_call_tools = 0;
        if (finish_reason_tok != -1) {
            int len = tok[finish_reason_tok].end - tok[finish_reason_tok].start;
            if (len == 10 && strncmp(chunk.data + tok[finish_reason_tok].start, "tool_calls", 10) == 0) {
                should_call_tools = 1;
            }
        } else if (tool_calls_tok != -1) {
            should_call_tools = 1;
        }

        if (should_call_tools && tool_calls_tok != -1 && tok[tool_calls_tok].type == JSMN_ARRAY) {
            int num_calls = tok[tool_calls_tok].size;
            int current_tok = tool_calls_tok + 1;

            for (int tc = 0; tc < num_calls; tc++) {
                if (tok[current_tok].type != JSMN_OBJECT) break;

                int call_id_tok = -1;
                int func_tok = -1;

                int end_pos = tok[current_tok].end;
                int j = current_tok + 1;
                while (j < r && tok[j].start < end_pos) {
                    if (tok[j].type == JSMN_STRING) {
                        int len = tok[j].end - tok[j].start;
                        if (len == 2 && strncmp(chunk.data + tok[j].start, "id", 2) == 0) {
                            call_id_tok = j + 1;
                        } else if (len == 8 && strncmp(chunk.data + tok[j].start, "function", 8) == 0) {
                            func_tok = j + 1;
                        }
                    }
                    j = json_skip_token(tok, r, j + 1);
                }

                int name_tok = -1;
                int args_tok = -1;
                if (func_tok != -1 && tok[func_tok].type == JSMN_OBJECT) {
                    int f_end = tok[func_tok].end;
                    int k = func_tok + 1;
                    while (k < r && tok[k].start < f_end) {
                        if (tok[k].type == JSMN_STRING) {
                            int len = tok[k].end - tok[k].start;
                            if (len == 4 && strncmp(chunk.data + tok[k].start, "name", 4) == 0) {
                                name_tok = k + 1;
                            } else if (len == 9 && strncmp(chunk.data + tok[k].start, "arguments", 9) == 0) {
                                args_tok = k + 1;
                            }
                        }
                        k = json_skip_token(tok, r, k + 1);
                    }
                }

                if (call_id_tok != -1 && name_tok != -1 && args_tok != -1) {
                    char *unescaped_id = unescape_json_string(chunk.data + tok[call_id_tok].start, tok[call_id_tok].end - tok[call_id_tok].start);
                    char *unescaped_name = unescape_json_string(chunk.data + tok[name_tok].start, tok[name_tok].end - tok[name_tok].start);
                    char *unescaped_args = unescape_json_string(chunk.data + tok[args_tok].start, tok[args_tok].end - tok[args_tok].start);

                    char *tool_output = NULL;

                    if (strcmp(unescaped_name, "execute_command") == 0) {
                        jsmn_parser arg_parser;
                        jsmntok_t arg_toks[64];
                        jsmn_init(&arg_parser);
                        int arg_r = jsmn_parse(&arg_parser, unescaped_args, strlen(unescaped_args), arg_toks, 64);
                        char *cmd_val = NULL;
                        for (int a = 1; a < arg_r; a++) {
                            if (arg_toks[a].type == JSMN_STRING && 
                                arg_toks[a].end - arg_toks[a].start == 7 &&
                                strncmp(unescaped_args + arg_toks[a].start, "command", 7) == 0) {
                                cmd_val = unescape_json_string(unescaped_args + arg_toks[a + 1].start, arg_toks[a + 1].end - arg_toks[a + 1].start);
                                break;
                            }
                        }

                        if (cmd_val) {
                            fprintf(stderr, "[infer] executing command: %s\n", cmd_val);
                            size_t cmd_len = strlen(cmd_val);
                            char *cmd_with_stderr = malloc(cmd_len + 16);
                            sprintf(cmd_with_stderr, "%s 2>&1", cmd_val);
                            tool_output = run_shell_command(cmd_with_stderr);
                            free(cmd_with_stderr);
                            free(cmd_val);
                        } else {
                            tool_output = strdup("Error: 'command' argument not found");
                        }
                    } else {
                        char *server_name = strdup(unescaped_name);
                        char *mcp_tool_name = strstr(server_name, "__");
                        if (mcp_tool_name) {
                            *mcp_tool_name = '\0';
                            mcp_tool_name += 2;
                        } else {
                            mcp_tool_name = unescaped_name;
                        }

                        fprintf(stderr, "[infer] calling MCP tool '%s' on server '%s'\n", mcp_tool_name, server_name);
                        
                        char *escaped_args_shell = shell_escape(unescaped_args);
                        char call_cmd[4096 + strlen(escaped_args_shell)];
                        snprintf(call_cmd, sizeof(call_cmd), "python3 %s call-tool %s %s %s", mcp_script, server_name, mcp_tool_name, escaped_args_shell);
                        tool_output = run_shell_command(call_cmd);

                        free(server_name);
                        free(escaped_args_shell);
                    }

                    if (!tool_output) {
                        tool_output = strdup("Error: failed to execute tool");
                    }

                    char *safe_output = json_escape(tool_output);
                    size_t tool_resp_len = strlen(safe_output) + strlen(unescaped_id) + strlen(unescaped_name) + 256;
                    char *tool_resp = malloc(tool_resp_len);
                    sprintf(tool_resp, "{\"role\":\"tool\",\"tool_call_id\":\"%s\",\"name\":\"%s\",\"content\":\"%s\"}", unescaped_id, unescaped_name, safe_output);
                    
                    messages_json = append_message(messages_json, tool_resp);

                    free(unescaped_id);
                    free(unescaped_name);
                    free(unescaped_args);
                    free(tool_output);
                    free(safe_output);
                    free(tool_resp);
                }

                current_tok = json_skip_token(tok, r, current_tok);
            }
        } else {
            has_more = 0;

            int content_tok = -1;
            if (message_tok != -1) {
                int msg_end = tok[message_tok].end;
                int k = message_tok + 1;
                while (k < r && tok[k].start < msg_end) {
                    if (tok[k].type == JSMN_STRING && 
                        tok[k].end - tok[k].start == 7 &&
                        strncmp(chunk.data + tok[k].start, "content", 7) == 0) {
                        content_tok = k + 1;
                        break;
                    }
                    k = json_skip_token(tok, r, k + 1);
                }
            }

            if (content_tok != -1 && tok[content_tok].type == JSMN_STRING) {
                char *unescaped_content = unescape_json_string(chunk.data + tok[content_tok].start, tok[content_tok].end - tok[content_tok].start);
                char *escaped_content = shell_escape(unescaped_content);
                
                char render_cmd[4096 + strlen(escaped_content)];
                snprintf(render_cmd, sizeof(render_cmd), "python3 %s render-markdown %s", mcp_script, escaped_content);
                char *rendered_output = run_shell_command(render_cmd);
                
                if (rendered_output) {
                    printf("%s", rendered_output);
                    free(rendered_output);
                } else {
                    printf("%s\n", unescaped_content);
                }
                
                free(unescaped_content);
                free(escaped_content);
            }
        }

        free(payload);
        free(chunk.data);
    }

    free(pipe_in);
    free(prompt);
    free(messages_json);
    if (tools_json) free(tools_json);
    curl_slist_free_all(h);
    curl_easy_cleanup(c);
    return 0;
}
