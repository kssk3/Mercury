import type { Metadata } from "next";
import "./globals.css";
export const metadata: Metadata = {
  title: "Mercury — 코드 변경을 확인할 근거",
  description: "AI의 코드 변경 이후, 허용된 파일과 선택한 검사의 결과를 확인하세요. 로컬 Python 도구 Mercury의 소개와 합성 데모입니다.",
};
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="ko"><body>{children}</body></html>;
}
