package main

// Encrypted storage for this device's Tailscale identity (node key, login).
//
// Tailscale's default store is a plain JSON file anyone running as you could
// read. Here the whole state is sealed before it touches the disk:
//   - macOS:   AES-256-GCM with a random key kept in your login Keychain
//   - Windows: DPAPI, so only your Windows account can decrypt it
// See seal_darwin.go / seal_windows.go.

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"sync"

	"tailscale.com/ipn"
)

const sealedName = "state.sealed"

type sealer interface {
	Seal(plain []byte) ([]byte, error)
	Open(sealed []byte) ([]byte, error)
	Forget() error // delete the key itself (on sign-out)
}

type secureStore struct {
	path  string
	seal  sealer
	mu    sync.Mutex
	state map[ipn.StateKey][]byte
}

func openSecureStore(dir string) (*secureStore, error) {
	sl, err := newSealer()
	if err != nil {
		return nil, err
	}
	s := &secureStore{path: filepath.Join(dir, sealedName), seal: sl, state: map[ipn.StateKey][]byte{}}
	if data, err := os.ReadFile(s.path); err == nil {
		plain, err := sl.Open(data)
		if err == nil && json.Unmarshal(plain, &s.state) == nil {
			return s, nil
		}
		// Can't decrypt (key deleted, different account...): keep the file aside and
		// start fresh, which just means signing in again.
		os.Rename(s.path, s.path+".unreadable")
		s.state = map[ipn.StateKey][]byte{}
		return s, nil
	} else if !errors.Is(err, os.ErrNotExist) {
		return nil, err
	}
	// First run with encryption: bring over Tailscale's plain-text file, then delete it.
	old := filepath.Join(dir, "tailscaled.state")
	if data, err := os.ReadFile(old); err == nil {
		if json.Unmarshal(data, &s.state) == nil {
			if err := s.save(); err != nil {
				return nil, err
			}
			os.Remove(old)
		}
	}
	return s, nil
}

func (s *secureStore) ReadState(id ipn.StateKey) ([]byte, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	v, ok := s.state[id]
	if !ok {
		return nil, ipn.ErrStateNotExist
	}
	return append([]byte(nil), v...), nil
}

func (s *secureStore) WriteState(id ipn.StateKey, bs []byte) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if bs == nil {
		delete(s.state, id)
	} else {
		s.state[id] = append([]byte(nil), bs...)
	}
	return s.save()
}

func (s *secureStore) save() error {
	plain, err := json.Marshal(s.state)
	if err != nil {
		return err
	}
	sealed, err := s.seal.Seal(plain)
	if err != nil {
		return err
	}
	tmp := s.path + ".tmp"
	if err := os.WriteFile(tmp, sealed, 0o600); err != nil {
		return err
	}
	return os.Rename(tmp, s.path)
}

// wipe removes the stored identity and its key (used by Sign out).
func (s *secureStore) wipe() error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.state = map[ipn.StateKey][]byte{}
	os.Remove(s.path)
	if err := s.seal.Forget(); err != nil {
		return err
	}
	sl, err := newSealer() // a brand-new key for whatever comes next
	if err != nil {
		return err
	}
	s.seal = sl
	return nil
}
