#ifndef AI_SESSION_H
#define AI_SESSION_H

#include <stddef.h>
#include <curl/curl.h>

char* read_memory_file(void);
char* load_system_prompt(void);
void save_session(const char *messages_json);
char* resume_session(const char *session_file);
char* compact_session(char *messages_json, const char *mcp_script, CURL *curl_handle, const char *model_name, int *out_success);
char* maybe_trim_messages(char *messages_json, const char *mcp_script);

char* load_session_transcript(char *messages_json, const char *mcp_script);
#endif /* AI_SESSION_H */
