// Global setup for the WI-7 unit suite.
//
// `fake-indexeddb/auto` installs an in-memory IndexedDB implementation on the
// global scope so utils/outboxDb.ts (real `idb` code) runs unchanged under Node.
import "fake-indexeddb/auto";
