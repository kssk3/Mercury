import { ArrowDown, ArrowUpRight, FileCode2, Fingerprint, FlaskConical, GitBranch, ShieldCheck, Terminal } from "lucide-react";
import { SiGithub } from "react-icons/si";
import { Demo } from "@/components/demo";
import { loadDemoReceipts } from "@/lib/demo-receipts";

const github = "https://github.com/kssk3/Mercury";
const quickstart = `${github}/blob/main/docs/quickstart.md`;
const situations = [
  { title: "버그 하나를 고쳤는데, 기존 동작이 바뀌었다면", question: "음수 결제는 고쳤는데 무료 배송까지 바뀐 건 아닐까?", help: "선택한 배송비 검사가 회귀를 발견합니다. Mercury는 그 결과를 변경 범위·실행 상태와 함께 기록합니다.", href: "#demo-regression", link: "배송비 회귀 사례 보기" },
  { title: "요청하지 않은 파일까지 수정됐다면", question: "cart.py만 고치라고 했는데 배송비 정책 파일도 바뀌었네?", help: "허용·보호 경로와 관측한 변경을 비교합니다. 승인 범위 위반과 후속 검사 미실행을 함께 확인합니다.", href: "#demo-scope-escape", link: "승인 범위 이탈 사례 보기" },
  { title: "일부 테스트 후 작업이 중단됐다면", question: "테스트가 통과했다는데, 최종 검사까지 끝난 건가?", help: "실행된 검사와 미실행 검사를 구분합니다. 최종 근거가 없으면 UNKNOWN으로 남겨 완료와 구분합니다.", href: "#demo-incomplete", link: "미완료 검증 사례 보기" },
];
const tools = [
  { name: "Git diff", does: "어떤 코드가 바뀌었는지 확인합니다.", connects: "그 변경을 사람이 정한 허용·보호 범위와 연결합니다." },
  { name: "테스트", does: "선택한 조건에서 기대대로 동작하는지 확인합니다.", connects: "어떤 검사 명령이 통과·실패·미실행 상태인지 함께 기록합니다." },
  { name: "CI", does: "설정된 워크플로의 검사 결과를 확인합니다.", connects: "Mercury로 수행한 로컬 AI 작업의 범위·검사·중단 기록을 검토할 근거를 제공합니다." },
];

export default function Home() {
  const receipts = loadDemoReceipts();
  return <>
    <a href="#main" className="skip-link">본문으로 건너뛰기</a>
    <header className="site-header container"><a href="#" className="brand" aria-label="Mercury 홈"><span className="brand-mark" aria-hidden="true">m<span /></span>Mercury</a><nav aria-label="주 메뉴"><a href="#how-it-works">작동 방식</a><a href="#demo">데모</a><a href={github} className="github-link"><SiGithub aria-hidden="true" /> GitHub <ArrowUpRight size={14} aria-hidden="true" /></a></nav></header>
    <main id="main">
      <section className="hero container" aria-labelledby="hero-title">
        <div className="hero-copy"><p className="preview-tag"><span /> 로컬 Python 도구 · 개발자 프리뷰</p><h1 id="hero-title">AI가 바꾼 코드,<br />어디까지 확인됐는지<br />함께 보세요.</h1><p className="hero-description">Mercury는 AI 코딩 작업에서 사람이 정한 변경 범위와 실제 검사 결과를 함께 기록하는 로컬 도구입니다.</p><p className="hero-context">Git diff와 테스트를 살펴볼 때, 범위 이탈과 실행되지 않은 검증까지 구분할 수 있도록 돕습니다.</p><div className="hero-actions"><a href="#demo" className="primary-button">장바구니 수정 결과 비교하기 <ArrowDown size={17} aria-hidden="true" /></a><a href="#get-started" className="text-link">로컬에서 체험하기 <ArrowUpRight size={17} aria-hidden="true" /></a></div><p className="hero-note">작업 범위와 검사 명령은 사람이 정합니다.</p></div>
        <div className="hero-visual" aria-label="이 작업에서 확인할 세 가지">
          <div className="record-heading"><span className="record-symbol"><GitBranch size={20} aria-hidden="true" /></span><div><strong>이 작업에서 확인할 세 가지</strong><span>장바구니 버그 수정 사례</span></div><span className="record-mode">예시</span></div>
          <div className="record-task"><span>사람이 정한 작업</span><p>쿠폰 적용 후 음수 금액 수정</p></div>
          <div className="record-timeline"><div><span className="timeline-icon"><FileCode2 size={18} aria-hidden="true" /></span><div><strong>승인한 파일 안에서 바뀌었나요?</strong><span>cart.py 허용 · shipping.py 보호</span></div></div><div><span className="timeline-icon"><Terminal size={18} aria-hidden="true" /></span><div><strong>필요한 검사가 실제로 실행됐나요?</strong><span>할인 검사 · 배송비 검사</span></div></div><div><span className="timeline-icon"><Fingerprint size={18} aria-hidden="true" /></span><div><strong>실패와 확인 불가를 구분했나요?</strong><span>검사 실패 · 범위 이탈 · 최종 검사 미실행</span></div></div></div>
          <div className="record-footer"><Fingerprint size={20} aria-hidden="true" /><p>변경 내용과 검사 근거를<br /><strong>사람이 함께 검토합니다.</strong></p></div>
          <span className="visual-caption">아래 데모에서 네 사례의 실제 기록을 비교하세요.</span>
        </div>
      </section>
      <section className="situations-section" id="use-cases" aria-labelledby="situations-title"><div className="container">
        <div className="section-heading"><div><h2 id="situations-title">AI 작업을 검토할 때,<br />이런 순간에 도움이 됩니다.</h2><p>변경 내용, 검사 로그, 작업 지시를 따로 대조하던 정보를 한 작업의 기록으로 연결합니다.</p></div></div>
        <div className="situations-grid">{situations.map((situation, i) => <article className="situation" key={situation.href}><span className="situation-number">0{i + 1}</span><h3>{situation.title}</h3><p className="situation-question">“{situation.question}”</p><p>{situation.help}</p><a className="text-link" href={situation.href}>{situation.link} <ArrowDown size={16} aria-hidden="true" /></a></article>)}</div>
      </div></section>
      <section className="tools-section container" aria-labelledby="tools-title"><div className="section-heading"><div><h2 id="tools-title">익숙한 개발 도구와<br />함께 사용합니다.</h2><p>Git diff·테스트·CI의 결과에, 사람이 정한 작업 범위와 실행 맥락을 연결합니다.</p></div></div>
        <div className="tools-grid">{tools.map(tool => <article className="tool-relationship" key={tool.name}><h3>{tool.name}</h3><p><span>기존 도구로 확인</span>{tool.does}</p><p><span>Mercury와 함께</span>{tool.connects}</p></article>)}</div>
        <p className="fit-note">이미 같은 정보를 작업별로 수집·비교하는 자동화가 있다면 추가 이점은 작을 수 있습니다. 변경 범위와 실행 완료 여부를 반복해서 확인하는 작업에서 먼저 평가해 보세요.</p>
      </section>
      <div className="container"><Demo receipts={receipts} /></div>
      <section className="how-section container" id="how-it-works" aria-labelledby="how-title"><div className="section-heading"><div><h2 id="how-title">내 작업에 적용하는 순서</h2><p>의미 있는 검사와 실행 승인은 사람이 준비합니다.</p></div></div><div className="how-grid application-grid">
        <article><span className="how-icon"><FileCode2 size={24} aria-hidden="true" /></span><h3>1. 작업과 범위를 정합니다</h3><p>바꿀 내용, 허용 파일, 보호할 입력을 선택합니다.</p></article>
        <article><span className="how-icon"><Terminal size={24} aria-hidden="true" /></span><h3>2. 검사 명령을 연결합니다</h3><p>기존 테스트 중 무엇으로 완료 여부를 확인할지 정합니다.</p></article>
        <article><span className="how-icon"><ShieldCheck size={24} aria-hidden="true" /></span><h3>3. 설정을 검토하고 승인합니다</h3><p>실행 설정을 확인한 뒤, 실제 실행을 별도로 승인합니다.</p></article>
        <article><span className="how-icon"><Fingerprint size={24} aria-hidden="true" /></span><h3>4. 기록을 읽고 판단합니다</h3><p>수정·추가 검증·사람 검토 중 필요한 다음 행동을 결정합니다.</p></article>
      </div><p className="boundary-note"><ShieldCheck size={18} aria-hidden="true" /> 범위 관찰은 OS 샌드박스가 아니며, 선택한 검사의 통과가 모든 동작의 정확성을 보장하지는 않습니다.</p></section>
      <section className="start-section" id="get-started" aria-labelledby="start-title"><div className="container start-grid"><div><span className="start-icon"><FlaskConical size={24} aria-hidden="true" /></span><h2 id="start-title">먼저, AI 없이<br />로컬 흐름을 체험하세요.</h2><p>Python 3.12 이상과 Git이 필요합니다.<br />브라우저 데모는 저장된 실행 기록이며 실제 AI를 호출하지 않습니다.</p><a href={`${quickstart}#recorded-cart-scenarios`} className="text-link">방금 본 장바구니 사례 재현하기 <ArrowUpRight size={17} aria-hidden="true" /></a><a href={quickstart} className="text-link start-secondary">기본 오프라인 quickstart 읽기 <ArrowUpRight size={17} aria-hidden="true" /></a></div><div className="quickstart-panel"><div className="terminal-heading"><Terminal size={16} aria-hidden="true" /><span>기본 예제 · 저장소 루트의 로컬 터미널</span></div><pre><code>{`python3.12 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/python examples/quickstart.py --approve-demo`}</code></pre><p><a href={`${github}/blob/main/examples/quickstart.py`}>기본 예제 코드를 먼저 읽으세요.</a> <code>--approve-demo</code>는 정해진 일회용 작업을 승인합니다. 임시 저장소에서 파일을 바꾸고 로컬 검사를 실행하지만 실제 AI나 모델은 호출하지 않습니다. 장바구니 사례는 별도 재현 안내를 따르세요.</p></div></div></section>
    </main>
    <footer className="site-footer container"><a href="#" className="brand"><span className="brand-mark" aria-hidden="true">m<span /></span>Mercury</a><p>코드 변경 이후, 판단을 위한 근거.</p><div><a href={quickstart}>문서</a><a href={github}>GitHub</a><a href={`${github}/blob/main/LICENSE`}>MIT 라이선스</a></div></footer>
  </>;
}
