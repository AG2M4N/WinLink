package com.asterinet.react.tcpsocket;

import java.util.concurrent.ExecutorService;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;

/**
 * WinLink's change: each socket used to get Executors.newSingleThreadExecutor()s that were never
 * shut down, so every connection left its threads behind for good. An app that connects often (a
 * status check every few seconds) ran out of threads after a few hours and crashed with
 * "OutOfMemoryError: pthread_create failed".
 *
 * These run one task at a time, in order, like a single-thread executor, but their thread ends after
 * a few idle seconds (and a new one starts for the next task), so a socket that's done holds none.
 */
final class Threads {
    private Threads() {
    }

    static ExecutorService single() {
        ThreadPoolExecutor e = new ThreadPoolExecutor(1, 1, 5, TimeUnit.SECONDS, new LinkedBlockingQueue<Runnable>());
        e.allowCoreThreadTimeOut(true);
        return e;
    }
}
