/** 整数 kHz -> 固定三位小数的 MHz 文本，与后端 format_mhz 口径一致 */
export function formatMhz(khz: number): string {
  return `${Math.floor(khz / 1000)}.${String(khz % 1000).padStart(3, "0")}`;
}
