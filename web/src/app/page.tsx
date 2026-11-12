import { ArrowDown, ArrowUpRight, Check, FileCode2, Fingerprint, FlaskConical, GitBranch, ShieldCheck, Terminal } from "lucide-react";
import { SiGithub } from "react-icons/si";
import { Demo } from "@/components/demo";
import { loadDemoReceipts } from "@/lib/demo-receipts";

const github = "https://github.com/kssk3/Mercury";
const quickstart = `${github}/blob/main/docs/quickstart.md`;

export default function Home() {
  const receipts = loadDemoReceipts();
  return <>
    <a href="#main" className="skip-link">본문으로 건너뛰기</a>
    <header className="site-header container"><a href="#" className="brand" aria-label="Mercury 홈"><span className="brand-mark" aria-hidden="true">m<span /></span>Mercury</a><nav aria-label="주 메뉴"><a href="#how-it-works">작동 방식</a><a href="#demo">데모</a><a href={github} className="github-link"><SiGithub aria-hidden="true" /> GitHub <ArrowUpRight size={14} aria-hidden="true" /></a></nav></header>
    <main id="main">
      <section className="hero container" aria-labelledby="hero-title">
        <div className="hero-copy"><p className="preview-tag"><span /> 로컬 Python 도구 · 개발자 프리뷰</p><h1 id="hero-title">AI가 바꾼 코드,<br />확인할 근거를<br />남기세요.</h1><p className="hero-description">어떤 파일이 바뀌었는지,<br className="desktop-break" /> 선택한 검사가 실제로 통과했는지.<br />Mercury는 사람이 검토할 실행 근거를 모읍니다.</p><div className="hero-actions"><a href="#demo" className="primary-button">데모 살펴보기 <ArrowDown size={17} aria-hidden="true" /></a><a href="#get-started" className="text-link">로컬에서 시작하기 <ArrowUpRight size={17} aria-hidden="true" /></a></div><p className="hero-note">작업과 검사 기준은 사람이 정합니다.</p></div>
        <div className="hero-visual" aria-label="장바구니 작업의 요구사항과 검사 흐름">
          <div className="record-heading"><span className="record-symbol"><GitBranch size={20} aria-hidden="true" /></span><div><strong>변경에서 확인까지</strong><span>장바구니 버그 수정 사례</span></div><span className="record-mode">예시</span></div>
          <div className="record-task"><span>사람이 정한 작업</span><p>쿠폰 적용 후 음수 금액 수정</p></div>
          <div className="record-timeline"><div><span className="timeline-icon"><FileCode2 size={18} aria-hidden="true" /></span><div><strong>cart.py</strong><span>허용된 파일 안에서 변경</span></div><span className="tiny-badge">관찰</span></div><div><span className="timeline-icon"><ShieldCheck size={18} aria-hidden="true" /></span><div><strong>check_discount.py · check_shipping.py</strong><span>보호된 검사는 그대로</span></div><Check size={18} aria-hidden="true" /><span className="sr-only">변경 없음</span></div><div><span className="timeline-icon"><Terminal size={18} aria-hidden="true" /></span><div><strong>선택한 검사</strong><span>개발 검사 + 별도 최종 검사</span></div><Check size={18} aria-hidden="true" /><span className="sr-only">예시에서 통과</span></div></div>
          <div className="record-footer"><Fingerprint size={20} aria-hidden="true" /><p>변경 내용과 검사 근거를<br /><strong>사람이 함께 검토합니다.</strong></p></div>
          <span className="visual-caption">아래 데모에서 네 사례의 실제 기록을 비교하세요.</span>
        </div>
      </section>
      <section className="principle-band" aria-label="Mercury의 역할"><div className="container principle-inner"><p>“완료했습니다”라는 설명에서<br /><strong>확인할 수 있는 근거로.</strong></p><p>에이전트는 코드를 바꿉니다.<br />Mercury는 변경과 검사 결과를 관찰하고 기록합니다.<br />최종 판단은 사람이 내립니다.</p></div></section>
      <div className="container"><Demo receipts={receipts} /></div>
      <section className="how-section container" id="how-it-works" aria-labelledby="how-title"><div className="section-heading"><div><h2 id="how-title">사람의 기준을 먼저,<br />관측한 결과를 그다음에.</h2><p>실제 Mercury는 로컬 저장소에서 이 흐름을 구성합니다.</p></div></div><div className="how-grid"><article><span className="how-icon"><FileCode2 size={24} aria-hidden="true" /></span><h3>작업의 경계를 정합니다</h3><p>바꿀 작업, 허용 파일, 보호할 입력, 의미 있는 검사 명령을 사람이 선택합니다.</p></article><article><span className="how-icon"><GitBranch size={24} aria-hidden="true" /></span><h3>변경과 검사를 관찰합니다</h3><p>선택한 에이전트의 변경 경로와 검사 명령의 결과를 모아 완료 기준과 비교합니다.</p></article><article><span className="how-icon"><Fingerprint size={24} aria-hidden="true" /></span><h3>근거를 읽고 판단합니다</h3><p>관측 기록에서 실패와 확인 불가를 구분합니다. 기술 결과와 리뷰 승인은 별개입니다.</p></article></div><p className="boundary-note"><ShieldCheck size={18} aria-hidden="true" /> 범위 관찰은 OS 샌드박스가 아니며, 선택한 검사의 통과가 모든 동작의 정확성을 보장하지는 않습니다.</p></section>
      <section className="start-section" id="get-started" aria-labelledby="start-title"><div className="container start-grid"><div><span className="start-icon"><FlaskConical size={24} aria-hidden="true" /></span><h2 id="start-title">먼저, AI 없이<br />로컬 흐름을 체험하세요.</h2><p>저장소를 받은 뒤 오프라인 시뮬레이터로 시작하세요.<br />Python 3.12 이상과 Git이 필요합니다.</p><a href={quickstart} className="text-link">오프라인 quickstart 읽기 <ArrowUpRight size={17} aria-hidden="true" /></a></div><div className="quickstart-panel"><div className="terminal-heading"><Terminal size={16} aria-hidden="true" /><span>저장소 루트에서 · 로컬 터미널</span></div><pre><code>{`python3.12 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/python examples/quickstart.py --approve-demo`}</code></pre><p><a href={`${github}/blob/main/examples/quickstart.py`}>예제 코드를 먼저 읽으세요.</a> <code>--approve-demo</code>는 정해진 일회용 작업을 승인합니다. 임시 저장소에서 파일을 바꾸고 로컬 검사를 실행하지만 실제 AI나 모델은 호출하지 않습니다.</p></div></div></section>
    </main>
    <footer className="site-footer container"><a href="#" className="brand"><span className="brand-mark" aria-hidden="true">m<span /></span>Mercury</a><p>코드 변경 이후, 판단을 위한 근거.</p><div><a href={quickstart}>문서</a><a href={github}>GitHub</a><a href={`${github}/blob/main/LICENSE`}>MIT 라이선스</a></div></footer>
  </>;
}
