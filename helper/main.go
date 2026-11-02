// linkts: WinLink's built-in Tailscale connection.
//
// Joins your tailnet as its own device (no Tailscale app needed) and gives
// WinLink three things over a private localhost control port:
//
//	<token> STATUS           -> one JSON line: state, sign-in URL, devices
//	<token> LOGIN            -> start interactive sign-in (URL shows up in STATUS)
//	<token> LOGOUT           -> sign out and erase this device's stored identity
//	<token> DIAL host:port   -> "OK" then a raw pipe to that tailnet address
//
// Tailnet connections to this device's WinLink port are forwarded to WinLink's
// local listener (-forward), prefixed with "FROM <ip>\n" so WinLink knows who
// is calling. The helper exits when WinLink closes its stdin.
//
// The device's identity is stored encrypted (see store.go), so you sign in once.
package main

import (
	"bufio"
	"context"
	"crypto/subtle"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"net"
	"os"
	"strings"
	"time"

	"tailscale.com/tsnet"
)

type device struct {
	Name    string   `json:"name"`
	IPs     []string `json:"ips"`
	Online  bool     `json:"online"`
	Direct  string   `json:"direct,omitempty"` // address of a direct connection, if any
	Relay   string   `json:"relay,omitempty"`  // relay (DERP) region used otherwise
	RxBytes int64    `json:"rx,omitempty"`
	TxBytes int64    `json:"tx,omitempty"`
}

type status struct {
	State     string   `json:"state"`
	KeyExpiry string   `json:"key_expiry"` // when Tailscale will ask to sign in again; "" = never
	AuthURL   string   `json:"auth_url"`
	Name      string   `json:"name"`
	IPs       []string `json:"ips"`
	Devices   []device `json:"devices"`
	Error     string   `json:"error,omitempty"`
}

func main() {
	dir := flag.String("dir", "", "where to keep this device's Tailscale state")
	hostname := flag.String("hostname", "link", "device name on the tailnet")
	port := flag.Int("port", 50555, "WinLink's port on the tailnet")
	forward := flag.String("forward", "", "WinLink's local listener for tailnet connections")
	flag.Parse()
	token := os.Getenv("LINKTS_TOKEN")

	if err := os.MkdirAll(*dir, 0o700); err != nil {
		fmt.Println("ERROR", err)
		os.Exit(1)
	}
	store, err := openSecureStore(*dir)
	if err != nil {
		fmt.Println("ERROR can't open secure storage:", err)
		os.Exit(1)
	}
	srv := &tsnet.Server{
		Dir:      *dir,
		Store:    store,
		Hostname: *hostname,
		Logf:     func(string, ...any) {},
		UserLogf: func(string, ...any) {},
	}
	if err := srv.Start(); err != nil {
		fmt.Println("ERROR", err)
		os.Exit(1)
	}
	defer srv.Close()
	if _, err := srv.LocalClient(); err != nil {
		fmt.Println("ERROR", err)
		os.Exit(1)
	}

	// Quit when WinLink goes away.
	go func() {
		io.Copy(io.Discard, os.Stdin)
		os.Exit(0) // state is already on disk; don't wait for a slow shutdown
	}()

	// Tailnet -> WinLink.
	go func() {
		for {
			ln, err := srv.Listen("tcp", fmt.Sprintf(":%d", *port))
			if err != nil {
				time.Sleep(2 * time.Second)
				continue
			}
			for {
				c, err := ln.Accept()
				if err != nil {
					break
				}
				go forwardIn(c, *forward)
			}
			ln.Close()
		}
	}()

	ctl, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		fmt.Println("ERROR", err)
		os.Exit(1)
	}
	fmt.Printf("READY %d\n", ctl.Addr().(*net.TCPAddr).Port)
	os.Stdout.Sync()

	for {
		c, err := ctl.Accept()
		if err != nil {
			continue
		}
		go handle(c, srv, store, token)
	}
}

func forwardIn(c net.Conn, to string) {
	defer c.Close()
	l, err := net.DialTimeout("tcp", to, 5*time.Second)
	if err != nil {
		return
	}
	defer l.Close()
	host, _, _ := net.SplitHostPort(c.RemoteAddr().String())
	fmt.Fprintf(l, "FROM %s\n", host)
	pipe(c, l)
}

func pipe(a, b net.Conn) {
	done := make(chan struct{}, 2)
	go func() { io.Copy(a, b); done <- struct{}{} }()
	go func() { io.Copy(b, a); done <- struct{}{} }()
	<-done
}

func handle(c net.Conn, srv *tsnet.Server, store *secureStore, token string) {
	defer c.Close()
	c.SetReadDeadline(time.Now().Add(10 * time.Second))
	line, err := bufio.NewReader(c).ReadString('\n')
	if err != nil {
		return
	}
	fields := strings.Fields(line)
	if len(fields) < 2 || subtle.ConstantTimeCompare([]byte(fields[0]), []byte(token)) != 1 {
		return
	}
	c.SetReadDeadline(time.Time{})
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	lc, _ := srv.LocalClient()

	switch fields[1] {
	case "STATUS":
		out := status{}
		st, err := lc.Status(ctx)
		if err != nil {
			out.State, out.Error = "Error", err.Error()
		} else {
			out.State, out.AuthURL = st.BackendState, st.AuthURL
			for _, ip := range st.TailscaleIPs {
				out.IPs = append(out.IPs, ip.String())
			}
			if st.Self != nil {
				out.Name = st.Self.HostName
				if st.Self.KeyExpiry != nil {
					out.KeyExpiry = st.Self.KeyExpiry.Format(time.RFC3339)
				}
			}
			for _, p := range st.Peer {
				d := device{Name: p.HostName, Online: p.Online, Direct: p.CurAddr, Relay: p.Relay,
					RxBytes: p.RxBytes, TxBytes: p.TxBytes}
				for _, ip := range p.TailscaleIPs {
					d.IPs = append(d.IPs, ip.String())
				}
				out.Devices = append(out.Devices, d)
			}
		}
		json.NewEncoder(c).Encode(out)
	case "LOGIN":
		if err := lc.StartLoginInteractive(ctx); err != nil {
			fmt.Fprintf(c, "ERR %v\n", err)
			return
		}
		fmt.Fprintln(c, "OK")
	case "LOGOUT":
		lc.Logout(ctx) // tell Tailscale; fine if offline
		if err := store.wipe(); err != nil {
			fmt.Fprintf(c, "ERR %v\n", err)
			return
		}
		fmt.Fprintln(c, "OK")
	case "DIAL":
		if len(fields) < 3 {
			return
		}
		r, err := srv.Dial(ctx, "tcp", fields[2])
		if err != nil {
			fmt.Fprintf(c, "ERR %v\n", err)
			return
		}
		defer r.Close()
		fmt.Fprintln(c, "OK")
		pipe(c, r)
	}
}
