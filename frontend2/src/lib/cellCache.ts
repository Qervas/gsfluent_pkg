/**
 * IndexedDB-backed cell cache.
 *
 * Cells are large (3GB scenes have ~30MB per frame at SH0+pos+scale).
 * Browser memory budgets get tight fast, so keeping them in IDB lets
 * us evict from RAM while staying re-fetchable without hitting MinIO
 * again on next visit.
 *
 * Schema: { id: artifactId, blob: Blob, fetchedAt: number }
 */

import { type DBSchema, type IDBPDatabase, openDB } from "idb";

const DB_NAME = "gsfluent-cells";
const STORE = "cells";
const VERSION = 1;

interface Schema extends DBSchema {
  cells: {
    key: string;
    value: { id: string; blob: Blob; fetchedAt: number };
    indexes: { "by-fetched": number };
  };
}

let _dbPromise: Promise<IDBPDatabase<Schema>> | null = null;

function getDb(): Promise<IDBPDatabase<Schema>> {
  if (!_dbPromise) {
    _dbPromise = openDB<Schema>(DB_NAME, VERSION, {
      upgrade(db) {
        const store = db.createObjectStore(STORE, { keyPath: "id" });
        store.createIndex("by-fetched", "fetchedAt");
      },
    });
  }
  return _dbPromise;
}

export async function getCachedBlob(artifactId: string): Promise<Blob | null> {
  const db = await getDb();
  const entry = await db.get(STORE, artifactId);
  return entry?.blob ?? null;
}

export async function putCachedBlob(artifactId: string, blob: Blob): Promise<void> {
  const db = await getDb();
  await db.put(STORE, { id: artifactId, blob, fetchedAt: Date.now() });
}

export async function evictOlderThan(ageMs: number): Promise<number> {
  const db = await getDb();
  const cutoff = Date.now() - ageMs;
  const tx = db.transaction(STORE, "readwrite");
  let n = 0;
  for await (const cursor of tx.store.index("by-fetched").iterate(IDBKeyRange.upperBound(cutoff))) {
    cursor.delete();
    n++;
  }
  await tx.done;
  return n;
}

export async function fetchOrCache(artifactId: string, signedUrl: string): Promise<Blob> {
  const cached = await getCachedBlob(artifactId);
  if (cached) return cached;
  const r = await fetch(signedUrl);
  if (!r.ok) throw new Error(`fetch ${artifactId}: ${r.status}`);
  const blob = await r.blob();
  await putCachedBlob(artifactId, blob);
  return blob;
}
