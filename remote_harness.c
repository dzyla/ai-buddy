/*
 * remote_harness.c — Remote server control harness implementation
 *
 * Provides SSH-based remote execution, auto-discovery, and job submission.
 * All commands execute as detached processes (non-blocking).
 */

#include "remote_harness.h"
#include <sys/stat.h>
#include <unistd.h>

/* ── Internal Helpers ───────────────────────────────────────────────────────── */

/* Build SSH command line */
static char* build_ssh_cmd(const char *hostname, int port, const char *username,
                           const char *password, const char *remote_cmd) {
    size_t len = 2048;
    char *cmd = malloc(len);
    if (password && *password) {
        snprintf(cmd, len, "sshpass -p '%s' ssh -o StrictHostKeyChecking=no "
                 "-o UserKnownHostsFile=/dev/null -p %d %s@%s '%s'",
                 password, port, username, hostname, remote_cmd);
    } else {
        snprintf(cmd, len, "ssh -o StrictHostKeyChecking=no "
                 "-o UserKnownHostsFile=/dev/null -p %d %s@%s '%s'",
                 port, username, hostname, remote_cmd);
    }
    return cmd;
}

/* Run command detached, capture output, wait for completion */
static char* run_command(const char *cmd, int timeout_sec, int *exit_code) {
    if (exit_code) *exit_code = 0;
    
    /* Write command to temp file */
    char tmpfile[] = "/tmp/ai_remote_cmd_XXXXXX";
    int fd = mkstemp(tmpfile);
    if (fd == -1) return NULL;
    if (write(fd, cmd, strlen(cmd)) < 0) {
        close(fd);
        unlink(tmpfile);
        return NULL;
    }
    close(fd);
    chmod(tmpfile, 0755);
    
    /* Run via timeout + sh, capture output */
    char runner_cmd[1024];
    if (timeout_sec > 0) {
        snprintf(runner_cmd, sizeof(runner_cmd), "timeout %d %s", timeout_sec, tmpfile);
    } else {
        snprintf(runner_cmd, sizeof(runner_cmd), "%s", tmpfile);
    }

    char outbuf[REMOTE_MAX_OUTPUT];
    FILE *fp = popen(runner_cmd, "r");
    if (!fp) {
        unlink(tmpfile);
        return NULL;
    }
    
    size_t total = 0;
    size_t n;
    while ((n = fread(outbuf + total, 1, sizeof(outbuf) - total - 1, fp)) > 0) {
        total += n;
        if (total >= sizeof(outbuf) - 1) break;
    }
    outbuf[total] = '\0';
    
    int rc = pclose(fp);
    if (exit_code) {
        *exit_code = WIFEXITED(rc) ? WEXITSTATUS(rc) : -1;
    }
    
    unlink(tmpfile);
    return strdup(outbuf);
}

/* ── Public API ─────────────────────────────────────────────────────────────── */

remote_server_t* remote_connect(const char *hostname, int port,
                                 const char *username, const char *password,
                                 const char *description) {
    if (!hostname || !username) return NULL;
    
    remote_server_t *srv = calloc(1, sizeof(remote_server_t));
    snprintf(srv->hostname, sizeof(srv->hostname), "%s", hostname);
    srv->port = port > 0 ? port : REMOTE_DEFAULT_PORT;
    snprintf(srv->username, sizeof(srv->username), "%s", username);
    if (password) snprintf(srv->password, sizeof(srv->password), "%s", password);
    if (description) snprintf(srv->description, sizeof(srv->description), "%s", description);
    srv->connected = 1;
    srv->last_connect = time(NULL);
    
    return srv;
}

void remote_disconnect(remote_server_t *server) {
    if (!server) return;
    server->connected = 0;
    free(server);
}

char* remote_exec(remote_server_t *server, const char *command, int timeout_sec) {
    if (!server || !command || !server->connected) return NULL;
    if (timeout_sec <= 0) timeout_sec = REMOTE_DEFAULT_TIMEOUT;
    
    char *ssh_cmd = build_ssh_cmd(server->hostname, server->port,
                                   server->username, server->password,
                                   command);
    
    int exit_code = 0;
    char *output = run_command(ssh_cmd, timeout_sec, &exit_code);
    free(ssh_cmd);
    
    if (!output) {
        return strdup("Error: failed to execute command");
    }
    if (exit_code != 0) {
        size_t len = strlen(output) + 128;
        char *err = malloc(len);
        snprintf(err, len, "failed (exit %d)\n%s", exit_code, output);
        free(output);
        return err;
    }
    
    /* Trim trailing whitespace */
    size_t len = strlen(output);
    while (len > 0 && (output[len-1] == '\n' || output[len-1] == '\r' || output[len-1] == ' ')) {
        output[--len] = '\0';
    }
    
    return output;
}

int remote_discover(remote_server_t *server) {
    if (!server || !server->connected) return 0;
    
    /* Discover OS info */
    char *os_out = remote_exec(server, 
        "uname -s && cat /etc/os-release | grep '^PRETTY_NAME' | cut -d= -f2 && uname -r && uname -m", 30);
    if (os_out) {
        char *lines[10];
        int n = 0;
        char *tok = strtok(os_out, "\n");
        while (tok && n < 10) {
            lines[n++] = strdup(tok);
            tok = strtok(NULL, "\n");
        }
        if (n >= 1) {
            strncpy(server->os_name, lines[0], sizeof(server->os_name) - 1);
            if (n >= 2) strncpy(server->os_version, lines[1], sizeof(server->os_version) - 1);
            if (n >= 3) strncpy(server->kernel_version, lines[2], sizeof(server->kernel_version) - 1);
            if (n >= 4) strncpy(server->arch, lines[3], sizeof(server->arch) - 1);
        }
        for (int i = 0; i < n; i++) free(lines[i]);
        free(os_out);
    }
    
    /* Discover CPU info */
    char *cpu_out = remote_exec(server, "nproc", 10);
    if (cpu_out) {
        server->num_cpus = atoi(cpu_out);
        free(cpu_out);
    }
    
    /* Discover RAM */
    char *mem_out = remote_exec(server, 
        "free -m | awk '/^Mem:/{print $2, $3, $7}'", 10);
    if (mem_out) {
        char *lines[10];
        int n = 0;
        char *tok = strtok(mem_out, "\n");
        while (tok && n < 10) {
            lines[n++] = strdup(tok);
            tok = strtok(NULL, "\n");
        }
        if (n >= 1) {
            int vals[3];
            if (sscanf(lines[0], "%d %d %d", &vals[0], &vals[1], &vals[2]) == 3) {
                server->total_memory_mb = vals[0];
                server->used_memory_mb = vals[1];
                server->free_memory_mb = vals[2];
            }
        }
        for (int i = 0; i < n; i++) free(lines[i]);
        free(mem_out);
    }
    
    /* Discover GPUs */
    char *gpu_cmd = "nvidia-smi --query-gpu=index,name,memory.total,memory.used,utilization.gpu,temperature.gpu,driver_version,compute_cap --format=csv,noheader 2>/dev/null || echo 'NO_GPUS'";
    char *gpu_out = remote_exec(server, gpu_cmd, 30);
    if (gpu_out) {
        if (strstr(gpu_out, "NO_GPUS")) {
            server->num_gpus = 0;
        } else {
            char *lines[10];
            int n = 0;
            char *tok = strtok(gpu_out, "\n");
            while (tok && n < REMOTE_MAX_GPUS) {
                lines[n++] = strdup(tok);
                tok = strtok(NULL, "\n");
            }
            server->num_gpus = n;
            for (int i = 0; i < n; i++) {
                char name[256] = {0};
                int idx, mem_total, mem_used, util, temp;
                char drv[64] = {0}, cc[32] = {0};
                if (sscanf(lines[i], "%d,%[^,],%d,%d,%d,%d,%[^,],%s",
                           &idx, name, &mem_total, &mem_used, &util, &temp, drv, cc) >= 8) {
                    server->gpus[i].id = idx;
                    strncpy(server->gpus[i].name, name, sizeof(server->gpus[i].name) - 1);
                    server->gpus[i].memory_total_mb = mem_total;
                    server->gpus[i].memory_used_mb = mem_used;
                    server->gpus[i].utilization_percent = util;
                    server->gpus[i].temperature_c = temp;
                    snprintf(server->gpus[i].driver_version, sizeof(server->gpus[i].driver_version), "%s", drv);
                    snprintf(server->gpus[i].cuda_version, sizeof(server->gpus[i].cuda_version), "%.16s", cc);
                }
                free(lines[i]);
            }
        }
        free(gpu_out);
    }
    
    /* Discover drives */
    char *drv_out = remote_exec(server,
        "df -BG --output=source,target,fstype,size,used,avail,pcent -x tmpfs -x devtmpfs -x squashfs 2>/dev/null | tail -n +2", 15);
    if (drv_out) {
        char *lines[20];
        int n = 0;
        char *tok = strtok(drv_out, "\n");
        while (tok && n < REMOTE_MAX_DRIVES) {
            lines[n++] = strdup(tok);
            tok = strtok(NULL, "\n");
        }
        server->num_drives = n;
        for (int i = 0; i < n; i++) {
            char src[256], tgt[256], fstype[32];
            char size_s[32], used_s[32], avail_s[32];
            int pct;
            if (sscanf(lines[i], "%[^ ] %[^ ] %[^ ] %[^ ] %[^ ] %[^ ] %d",
                       src, tgt, fstype, size_s, used_s, avail_s, &pct) == 7) {
                strncpy(server->drives[i].device, src, sizeof(server->drives[i].device) - 1);
                strncpy(server->drives[i].mount_point, tgt, sizeof(server->drives[i].mount_point) - 1);
                strncpy(server->drives[i].filesystem_type, fstype, sizeof(server->drives[i].filesystem_type) - 1);
                server->drives[i].usage_percent = pct;
                server->drives[i].total_gb = atoi(size_s);
                server->drives[i].used_gb = atoi(used_s);
                server->drives[i].available_gb = atoi(avail_s);
                server->drives[i].is_network = (strstr(fstype, "nfs") || strstr(fstype, "cifs") || strstr(fstype, "smb") || strstr(fstype, "fuse"));
            }
            free(lines[i]);
        }
        free(drv_out);
    }
    
    /* Discover job submission system */
    char *js_out = remote_exec(server, "which sbatch pbsnodes qsub bsub 2>/dev/null", 10);
    if (js_out) {
        if (strstr(js_out, "sbatch")) {
            server->job_system.system_type = JOB_SLURM;
            strncpy(server->job_system.queue_name, "all", sizeof(server->job_system.queue_name) - 1);
        } else if (strstr(js_out, "pbsnodes")) {
            server->job_system.system_type = JOB_PBS_PRO;
            strncpy(server->job_system.queue_name, "default", sizeof(server->job_system.queue_name) - 1);
        } else if (strstr(js_out, "qsub")) {
            server->job_system.system_type = JOB_PBS_OPEN;
            strncpy(server->job_system.queue_name, "default", sizeof(server->job_system.queue_name) - 1);
        }
        free(js_out);
    }
    
    /* Discover environment */
    char *env_cmd = "echo $HOME && pwd && echo $SHELL && hostname";
    char *env_out = remote_exec(server, env_cmd, 10);
    if (env_out) {
        char *lines[10];
        int n = 0;
        char *tok = strtok(env_out, "\n");
        while (tok && n < 10) {
            lines[n++] = strdup(tok);
            tok = strtok(NULL, "\n");
        }
        if (n >= 1) strncpy(server->home_dir, lines[0], sizeof(server->home_dir) - 1);
        if (n >= 2) strncpy(server->working_dir, lines[1], sizeof(server->working_dir) - 1);
        if (n >= 3) strncpy(server->shell, lines[2], sizeof(server->shell) - 1);
        if (n >= 4) strncpy(server->hostname_remote, lines[3], sizeof(server->hostname_remote) - 1);
        for (int i = 0; i < n; i++) free(lines[i]);
        free(env_out);
    }
    
    server->has_discovered = 1;
    server->last_discovery = time(NULL);
    return 1;
}

char* remote_get_status(remote_server_t *server) {
    if (!server || !server->connected) return NULL;
    
    char *cpu_pct = remote_exec(server, 
        "top -bn1 | grep 'Cpu(s)' | awk '{print $2}' || echo 'N/A'", 10);
    char *mem_out = remote_exec(server,
        "free -m | awk '/^Mem:/{printf \"%.1f%%\", $3/$2*100}'", 10);
    char *disk_out = remote_exec(server,
        "df -h / | awk 'NR==2{print $5}'", 10);
    
    char status[2048];
    snprintf(status, sizeof(status),
        "CPU: %s%% | RAM: %s | Disk: %s\n"
        "OS: %s %s | Arch: %s\n"
        "CPUs: %d | RAM: %ldMB / %ldMB | Free: %ldMB\n"
        "GPUs: %d",
        cpu_pct ? cpu_pct : "N/A",
        mem_out ? mem_out : "N/A",
        disk_out ? disk_out : "N/A",
        server->os_name, server->os_version, server->arch,
        server->num_cpus, server->total_memory_mb,
        server->total_memory_mb - server->free_memory_mb,
        server->free_memory_mb,
        server->num_gpus);
    
    free(cpu_pct);
    free(mem_out);
    free(disk_out);
    
    return strdup(status);
}

char* remote_mount_drive(remote_server_t *server, const char *server_path,
                         const char *mount_point) {
    if (!server || !server->connected || !server_path || !mount_point) return NULL;
    
    char *cmd = malloc(1024);
    snprintf(cmd, 1024, 
        "mkdir -p %s && sudo mount -t nfs %s %s 2>/dev/null || "
        "sudo mount -t cifs '%s' %s -o username=%s 2>/dev/null || "
        "echo 'Mount failed - check permissions and network'",
        mount_point, server_path, mount_point, server_path, mount_point,
        server->username);
    
    char *output = remote_exec(server, cmd, 60);
    free(cmd);
    return output;
}

char* remote_submit_job(remote_server_t *server, const char *script,
                        const char *queue, int walltime_min, int num_nodes,
                        int num_cpus, int memory_gb) {
    if (!server || !server->connected || !script) return NULL;
    if (!server->job_system.system_type) {
        return strdup("Error: no job submission system detected");
    }
    
    char *cmd = malloc(2048);
    int hrs = walltime_min / 60;
    int mins = walltime_min % 60;
    
    if (server->job_system.system_type == JOB_SLURM) {
        snprintf(cmd, 2048,
            "sbatch --partition=%s --time=%d:%02d:00 --nodes=%d --ntasks=%d "
            "--mem=%dG --job-name='ai-buddy-job' /tmp/ai_job_%d.sh << 'SCRIPT'\n%s\nSCRIPT",
            queue ? queue : server->job_system.queue_name,
            hrs, mins, num_nodes, num_cpus, memory_gb, rand() % 9999, script);
    } else if (server->job_system.system_type == JOB_PBS_OPEN ||
               server->job_system.system_type == JOB_PBS_PRO) {
        snprintf(cmd, 2048,
            "qsub -q %s -l walltime=%d:%02d:00 -l select=%d:ncpus=%d:mem=%dg "
            "-l job_name='ai-buddy-job' /tmp/ai_job_%d.sh << 'SCRIPT'\n%s\nSCRIPT",
            queue ? queue : server->job_system.queue_name,
            hrs, mins, num_nodes, num_cpus, memory_gb, rand() % 9999, script);
    } else {
        snprintf(cmd, 2048, "echo 'Unsupported job system: %d'", server->job_system.system_type);
    }
    
    char *output = remote_exec(server, cmd, 30);
    free(cmd);
    return output;
}

char* remote_list_jobs(remote_server_t *server, const char *user, int max_jobs) {
    if (!server || !server->connected) return NULL;
    if (!user) user = server->username;
    
    char *cmd = malloc(512);
    if (server->job_system.system_type == JOB_SLURM) {
        snprintf(cmd, 512, "squeue -u %s -o '%%.10i %%.8u %%.10T %%.5M %%.10l %%.10D %%.10R' -h | head -n %d",
                 user, max_jobs);
    } else {
        snprintf(cmd, 512, "showq -u %s | grep '^ *%s' | head -n %d", user, user, max_jobs);
    }
    
    char *output = remote_exec(server, cmd, 30);
    free(cmd);
    return output;
}

char* remote_cancel_job(remote_server_t *server, int job_id) {
    if (!server || !server->connected) return NULL;
    
    char *cmd = malloc(128);
    if (server->job_system.system_type == JOB_SLURM) {
        snprintf(cmd, 128, "scancel %d", job_id);
    } else {
        snprintf(cmd, 128, "qdel %d", job_id);
    }
    
    char *output = remote_exec(server, cmd, 10);
    free(cmd);
    return output;
}

char* remote_job_status(remote_server_t *server, int job_id) {
    if (!server || !server->connected) return NULL;
    
    char *cmd = malloc(128);
    if (server->job_system.system_type == JOB_SLURM) {
        snprintf(cmd, 128, "scontrol show job %d", job_id);
    } else {
        snprintf(cmd, 128, "qstat -f %d", job_id);
    }
    
    char *output = remote_exec(server, cmd, 10);
    free(cmd);
    return output;
}
