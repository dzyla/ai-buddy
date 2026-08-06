#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "../remote_harness.h"

void test_remote_connect_disconnect(void) {
    remote_server_t *srv = remote_connect("localhost", 22, "testuser", "testpass", "Local Server");
    assert(srv != NULL);
    assert(strcmp(srv->hostname, "localhost") == 0);
    assert(srv->port == 22);
    assert(strcmp(srv->username, "testuser") == 0);
    assert(strcmp(srv->password, "testpass") == 0);
    assert(strcmp(srv->description, "Local Server") == 0);
    assert(srv->connected == 1);

    remote_disconnect(srv);
    printf("test_remote_connect_disconnect PASSED\n");
}

void test_remote_connect_null(void) {
    remote_server_t *srv = remote_connect(NULL, 22, "user", NULL, NULL);
    assert(srv == NULL);

    srv = remote_connect("localhost", 22, NULL, NULL, NULL);
    assert(srv == NULL);

    printf("test_remote_connect_null PASSED\n");
}

int main(void) {
    printf("Running remote_harness C unit tests...\n");
    test_remote_connect_disconnect();
    test_remote_connect_null();
    printf("All C unit tests PASSED.\n");
    return 0;
}
