import { getApps, initializeApp, type FirebaseApp } from 'firebase/app';
import { getAuth, type Auth } from 'firebase/auth';

const firebaseConfig = {
  apiKey: import.meta.env.VITE_FIREBASE_API_KEY ?? 'AIzaSyD8AFSR1vg21WOwLNVhczWfWfi3YSmZ9NA',
  authDomain: import.meta.env.VITE_FIREBASE_AUTH_DOMAIN ?? 'xno-quant.firebaseapp.com',
  projectId: import.meta.env.VITE_FIREBASE_PROJECT_ID ?? 'xno-quant',
  storageBucket: import.meta.env.VITE_FIREBASE_STORAGE_BUCKET ?? 'xno-quant.firebasestorage.app',
  messagingSenderId: import.meta.env.VITE_FIREBASE_MESSAGING_SENDER_ID ?? '188683570140',
  appId: import.meta.env.VITE_FIREBASE_APP_ID ?? '1:188683570140:web:cc43a99843e19ebef35d19',
};

export const firebaseEnabled = Boolean(firebaseConfig.apiKey && firebaseConfig.projectId && firebaseConfig.appId);

let app: FirebaseApp | undefined;
let auth: Auth | undefined;

if (firebaseEnabled) {
  app = getApps().length ? getApps()[0] : initializeApp(firebaseConfig);
  auth = getAuth(app);
}

export { app, auth };
