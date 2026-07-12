import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // Imagen Docker / EasyPanel: servidor autónomo sin node_modules completo
  output: "standalone",
};

export default nextConfig;
