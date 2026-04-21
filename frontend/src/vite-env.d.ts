/// <reference types="vite/client" />

declare module "*.css";

// @svg-maps/usa ships a d.ts that imports from svg-maps__common, which is not
// published as a separate package. Declare a minimal local shape so TS is happy.
declare module "@svg-maps/usa" {
  interface SvgMapLocation {
    name: string;
    id: string;
    path: string;
  }
  interface SvgMap {
    label: string;
    viewBox: string;
    locations: SvgMapLocation[];
  }
  const map: SvgMap;
  export default map;
}
