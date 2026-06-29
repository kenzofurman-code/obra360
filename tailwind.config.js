/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        // Paleta obra: concreto, aço, sinalização
        concreto: {
          950: '#0f1012',
          900: '#1a1c20',
          800: '#24272d',
          700: '#2e323a',
          600: '#3c424d',
        },
        aco: {
          400: '#8a9ab0',
          300: '#adbccc',
          200: '#cdd8e3',
        },
        sinal: {
          500: '#f59e0b', // amarelo obra
          400: '#fbbf24',
        },
        ok: '#22c55e',
        alerta: '#ef4444',
      },
      fontFamily: {
        mono: ['JetBrains Mono', 'Consolas', 'monospace'],
        sans: ['Inter', 'system-ui', 'sans-serif'],
      },
    },
  },
  plugins: [],
}
