package main

import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"os"
	"os/exec"
	"strings"
)

// The key lives in your login Keychain as "WinLink Tailscale key"; the encrypted
// state lives on disk. Neither is useful without the other, and the key never
// leaves this Mac (it isn't synced, backed up with the file, or in Nextcloud).
//
// The item is created so WinLink's helper can read it without a prompt. WinLink
// isn't signed with an Apple developer certificate, so macOS would otherwise
// treat every update as a new app and ask for your password again.
const keychainService = "WinLink Tailscale key"
const oldKeychainService = "Link Tailscale key" // before the rename

func keychainAccount() string {
	if a := os.Getenv("LINKTS_KEYCHAIN_ACCOUNT"); a != "" { // tests use their own item
		return a
	}
	return "state-encryption-key"
}

func security(args ...string) (string, error) {
	out, err := exec.Command("/usr/bin/security", args...).Output()
	return strings.TrimSpace(string(out)), err
}

type keychainSealer struct{ aead cipher.AEAD }

func newSealer() (sealer, error) {
	key, err := loadOrCreateKey()
	if err != nil {
		return nil, err
	}
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, err
	}
	aead, err := cipher.NewGCM(block)
	if err != nil {
		return nil, err
	}
	return &keychainSealer{aead}, nil
}

func loadOrCreateKey() ([]byte, error) {
	if out, err := security("find-generic-password", "-s", keychainService, "-a", keychainAccount(), "-w"); err == nil {
		if key, err := hex.DecodeString(out); err == nil && len(key) == 32 {
			return key, nil
		}
	}
	// Saved before the rename? Move it to the new name so the sign-in still opens.
	if out, err := security("find-generic-password", "-s", oldKeychainService, "-a", keychainAccount(), "-w"); err == nil {
		if key, err := hex.DecodeString(out); err == nil && len(key) == 32 {
			if saveKey(key) == nil {
				security("delete-generic-password", "-s", oldKeychainService, "-a", keychainAccount())
			}
			return key, nil
		}
	}
	key := make([]byte, 32)
	if _, err := rand.Read(key); err != nil {
		return nil, err
	}
	if err := saveKey(key); err != nil {
		return nil, err
	}
	return key, nil
}

func saveKey(key []byte) error {
	_, err := security("add-generic-password", "-U", "-A",
		"-s", keychainService, "-a", keychainAccount(), "-l", keychainService,
		"-j", "Encrypts WinLink's Tailscale sign-in", "-w", hex.EncodeToString(key))
	if err != nil {
		return errors.New("couldn't save a key in your Keychain")
	}
	return nil
}

func (k *keychainSealer) Seal(plain []byte) ([]byte, error) {
	nonce := make([]byte, k.aead.NonceSize())
	if _, err := rand.Read(nonce); err != nil {
		return nil, err
	}
	return k.aead.Seal(nonce, nonce, plain, []byte("link-tailscale-state")), nil
}

func (k *keychainSealer) Open(sealed []byte) ([]byte, error) {
	n := k.aead.NonceSize()
	if len(sealed) < n {
		return nil, errors.New("stored sign-in is damaged")
	}
	return k.aead.Open(nil, sealed[:n], sealed[n:], []byte("link-tailscale-state"))
}

func (k *keychainSealer) Forget() error {
	security("delete-generic-password", "-s", keychainService, "-a", keychainAccount())
	return nil // already gone is fine
}
