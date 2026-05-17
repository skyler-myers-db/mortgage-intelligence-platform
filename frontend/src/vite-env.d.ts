/// <reference types="vite/client" />

declare module "*.css";

declare module "us-atlas/*.json" {
  const topology: unknown;
  export default topology;
}
