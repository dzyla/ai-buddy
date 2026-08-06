/*
 * remote_harness.h — Remote server control harness for ai CLI
 *
 * Provides SSH-based remote server management with auto-discovery,
 * resource monitoring, and job submission capabilities.
 *
 * Usage:
 *   remote_server_t *server = remote_connect("192.168.1.100", "user", "pass", 22, "My Cluster");
 *   char *out = remote_exec(server, "ls -la /home", 120);
 *   remote_discover(server);
 *   remote_disconnect(server);
 */

#ifndef REMOTE_HARNESS_H
#define REMOTE_HARNESS_H

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#ifdef _WIN32
#include <winsock2.h>
#pragma comment(lib, "ws2_32.lib")
#else
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <unistd.h>
#include <sys/wait.h>
#define INVALID_SOCKET -1
#define SOCKET int
#define closesocket close
#endif

/* ── Constants ──────────────────────────────────────────────────────────────── */
#define REMOTE_MAX_OUTPUT   131072  /* 128KB max output buffer */
#define REMOTE_DEFAULT_PORT 22
#define REMOTE_DEFAULT_TIMEOUT 120  /* 2 minute timeout */
#define REMOTE_MAX_DRIVES   16
#define REMOTE_MAX_JOBS     64
#define REMOTE_MAX_GPUS     8

/* ── GPU Information ────────────────────────────────────────────────────────── */
typedef struct {
    int id;
    char name[256];
    int memory_total_mb;
    int memory_used_mb;
    int utilization_percent;
    int temperature_c;
    char driver_version[64];
    char cuda_version[32];
} remote_gpu_t;

/* ── Drive Information ──────────────────────────────────────────────────────── */
typedef struct {
    char mount_point[256];
    char device[256];
    char filesystem_type[32];
    long total_gb;
    long used_gb;
    long available_gb;
    int usage_percent;
    int is_network;  /* 1 if NFS/SMB/CIFS */
} remote_drive_t;

/* ── Job Submission System ──────────────────────────────────────────────────── */
typedef enum {
    JOB_NONE = 0,
    JOB_SLURM,
    JOB_PBS_PRO,
    JOB_PBS_OPEN,
    JOB_SGE,
    JOB_LSF,
    JOB_LOCAL
} job_system_t;

typedef struct {
    job_system_t system_type;
    char queue_name[64];
    int max_walltime_hours;
    int max_nodes;
    int max_cpus_per_node;
    int max_memory_gb;
} job_system_info_t;

/* ── Job Status ─────────────────────────────────────────────────────────────── */
typedef enum {
    JOB_STATE_PENDING = 0,
    JOB_STATE_RUNNING,
    JOB_STATE_COMPLETED,
    JOB_STATE_FAILED,
    JOB_STATE_CANCELLED,
    JOB_STATE_UNKNOWN
} job_state_t;

typedef struct {
    int job_id;
    char name[128];
    job_state_t state;
    int num_nodes;
    int num_cpus;
    double cpu_time_hours;
    double memory_used_gb;
    double walltime_hours;
    char submit_time[64];
    char start_time[64];
    char end_time[64];
    char partition[64];
} remote_job_t;

/* ── Remote Server Structure ───────────────────────────────────────────────── */
typedef struct {
    /* Connection details */
    char hostname[256];
    int port;
    char username[128];
    char password[256];
    char description[256];
    int connected;
    pid_t ssh_pid;
    int stdin_fd;
    int stdout_fd;
    int stderr_fd;
    
    /* Discovered resources */
    int has_discovered;
    char os_name[128];
    char os_version[64];
    char kernel_version[64];
    char arch[32];
    int num_cpus;
    long total_memory_mb;
    long used_memory_mb;
    long free_memory_mb;
    
    /* GPUs */
    int num_gpus;
    remote_gpu_t gpus[REMOTE_MAX_GPUS];
    
    /* Drives */
    int num_drives;
    remote_drive_t drives[REMOTE_MAX_DRIVES];
    
    /* Job submission */
    job_system_info_t job_system;
    int num_jobs;
    remote_job_t jobs[REMOTE_MAX_JOBS];
    
    /* Environment */
    char home_dir[512];
    char working_dir[512];
    char shell[128];
    char hostname_remote[128];
    
    /* Timestamps */
    time_t last_discovery;
    time_t last_connect;
} remote_server_t;

/* ── API Functions ──────────────────────────────────────────────────────────── */

/* Connect to remote server via SSH (password or key-based auth) */
remote_server_t* remote_connect(const char *hostname, int port, 
                                 const char *username, const char *password,
                                 const char *description);

/* Disconnect and free resources */
void remote_disconnect(remote_server_t *server);

/* Execute command on remote server (returns output, NULL on error) */
char* remote_exec(remote_server_t *server, const char *command, int timeout_sec);

/* Auto-discover server resources (OS, CPU, RAM, GPUs, drives, job system) */
int remote_discover(remote_server_t *server);

/* Get current resource usage (CPU%, RAM%, disk%, GPU utilization) */
char* remote_get_status(remote_server_t *server);

/* Map a network drive (NFS/SMB/CIFS) */
char* remote_mount_drive(remote_server_t *server, const char *server_path, 
                         const char *mount_point);

/* Submit a job to the cluster job scheduler */
char* remote_submit_job(remote_server_t *server, const char *script, 
                        const char *queue, int walltime_min, int num_nodes, 
                        int num_cpus, int memory_gb);

/* List jobs (submitted by user or all if admin) */
char* remote_list_jobs(remote_server_t *server, const char *user, int max_jobs);

/* Cancel a running/pending job */
char* remote_cancel_job(remote_server_t *server, int job_id);

/* Check job status */
char* remote_job_status(remote_server_t *server, int job_id);

#endif /* REMOTE_HARNESS_H */
