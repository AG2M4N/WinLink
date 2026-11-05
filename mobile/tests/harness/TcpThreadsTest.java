package com.asterinet.react.tcpsocket;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.TimeUnit;

/**
 * The phone app's socket threads (vendor/react-native-tcp-socket, Threads.java): each socket's
 * executors must run tasks in order and must not keep a thread once idle. Before, every connection
 * left two threads behind, and the app crashed after a few hours. Plain Java: Threads has no Android
 * dependencies. Run: javac -d out Threads.java TcpThreadsTest.java && java -cp out com.asterinet.react.tcpsocket.TcpThreadsTest
 */
public class TcpThreadsTest {
    static int fails = 0;

    static void check(String what, boolean ok) {
        System.out.println((ok ? "PASS " : "FAIL ") + what);
        if (!ok) fails++;
    }

    public static void main(String[] args) throws Exception {
        int before = Thread.activeCount();

        // 1000 connections, each with a listen and a write executor, each used once (as a socket is).
        List<ExecutorService> all = new ArrayList<>();
        CountDownLatch ran = new CountDownLatch(2000);
        for (int i = 0; i < 1000; i++) {
            ExecutorService listen = Threads.single();
            ExecutorService write = Threads.single();
            listen.execute(ran::countDown);
            write.execute(ran::countDown);
            all.add(listen);
            all.add(write);
        }
        check("all 2000 tasks ran", ran.await(30, TimeUnit.SECONDS));
        int busy = Thread.activeCount();
        System.out.println("threads: " + before + " before, " + busy + " just after");

        Thread.sleep(8000);                        // idle threads end after 5 s
        int after = Thread.activeCount();
        check("idle sockets hold no threads (" + after + " threads now, " + before + " before)", after <= before + 2);

        // Order: one task at a time, in the order given, even across a thread ending and a new one starting.
        ExecutorService e = Threads.single();
        StringBuilder order = new StringBuilder();
        CountDownLatch done = new CountDownLatch(1);
        for (int i = 0; i < 5; i++) {
            final int n = i;
            e.execute(() -> {
                try {
                    Thread.sleep(20);
                } catch (InterruptedException ignored) {
                }
                synchronized (order) {
                    order.append(n);
                }
            });
        }
        e.execute(done::countDown);
        done.await(10, TimeUnit.SECONDS);
        check("tasks run in order: " + order, order.toString().equals("01234"));
        Thread.sleep(6000);
        CountDownLatch again = new CountDownLatch(1);
        e.execute(again::countDown);
        check("a socket idle for a while can still write (a new thread starts)", again.await(5, TimeUnit.SECONDS));

        System.out.println(fails == 0 ? "\nAll passed." : "\n" + fails + " failed.");
        System.exit(fails == 0 ? 0 : 1);
    }
}
