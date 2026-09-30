/** 挂在子路径下也能跑。
 *
 *  默认自托管是 `http://127.0.0.1:8910/`，前端在站点根目录，`/api/...` 直接能用。
 *  但如果你把它挂在已有站点的一个子路径下（反向代理到 `/astock/` 这种），
 *  前端仍然请求绝对路径 `/api/...` —— 那会打到宿主站点上，不是这个后端。
 *
 *  所以所有请求地址都过一遍这里：构建时 `vite build --base=/astock/`，
 *  `import.meta.env.BASE_URL` 就是 `/astock/`，请求自动变成 `/astock/api/...`。
 *  不带 `--base` 时它是 `/`，行为与从前完全一致。
 */
export function apiUrl(path: string): string {
  const base = import.meta.env.BASE_URL || "/";
  return (base.endsWith("/") ? base.slice(0, -1) : base) + path;
}
