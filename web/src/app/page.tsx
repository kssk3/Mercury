import { ArrowDown, ArrowUpRight, FileCode2, Fingerprint, FlaskConical, GitBranch, ShieldCheck, Terminal } from "lucide-react";
import { SiGithub } from "react-icons/si";
import { Demo } from "@/components/demo";
import { loadDemoReceipts } from "@/lib/demo-receipts";

const github = "https://github.com/kssk3/Mercury";
const quickstart = `${github}/blob/main/docs/quickstart.md`;
const runGuide = `${github}/blob/main/docs/run-preview.md`;
const scopeExample = JSON.stringify({ contract: {
  goal: "쿠폰 적용 후 음수 결제 금액 수정, 배송비 정책 유지",
  allowed_paths: ["cart.py"],
  protected_paths: ["shipping.py", "check_discount.py", "check_shipping.py"],
  completion_criteria: ["할인 검사가 통과한다", "기존 배송비 검사가 통과한다"],
} }, null, 2);
const discountCommand = ["/absolute/path/to/python3", "-B", "check_discount.py"];
const shippingCommand = ["/absolute/path/to/python3", "-B", "check_shipping.py"];
const checksExample = JSON.stringify({
  verification: { commands: [discountCommand, shippingCommand], working_directory: ".", timeout_seconds: 5 },
  criterion_commands: { "할인 검사가 통과한다": [discountCommand], "기존 배송비 검사가 통과한다": [shippingCommand] },
}, null, 2);
const resultLinks = [
  { href: "#demo-correct", label: "정상 수정 기록" },
  { href: "#demo-regression", label: "회귀 기록" },
  { href: "#demo-scope-escape", label: "범위 이탈 기록" },
  { href: "#demo-incomplete", label: "검증 불완전 기록" },
];
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
      <section className="how-section container adoption-section" id="how-it-works" aria-labelledby="how-title">
        <div className="section-heading"><div><h2 id="how-title">내 프로젝트에서는 이렇게 사용합니다.</h2><p>바꿔도 되는 파일과 완료를 확인할 검사를 정하세요.<br />Mercury는 실행 기록을 남기고, 최종 판단은 사람이 합니다.</p></div></div>
        <div className="adoption-task"><span className="workspace-label">한 작업으로 따라가기</span><p><strong>쿠폰 적용 후 결제 금액이 음수가 되지 않게 수정합니다.</strong><br />할인 전 합계로 결정하는 기존 배송비 정책은 유지합니다.</p><p className="adoption-note">아래는 내 프로젝트 적용을 설명하는 설정 예시입니다. 위 데모 기록을 만든 정확한 실행 설정이 아니며, 브라우저에서 명령을 실행하지 않습니다.</p></div>
        <ol className="adoption-steps">
          <li><article className="adoption-step" aria-labelledby="adoption-scope-title"><div className="adoption-copy"><span className="how-icon"><FileCode2 size={24} aria-hidden="true" /></span><h3 id="adoption-scope-title">1. 작업 범위 정하기</h3><p>고칠 파일과 지켜야 할 파일을 나눕니다. 검사 파일을 보호해 수정 코드가 기준을 바꾸지 못하게 관찰합니다.</p><p>완료 기준은 <strong>할인 검사 통과</strong>와 <strong>기존 배송비 검사 통과</strong>입니다. 범위 관찰은 OS 샌드박스나 자동 롤백이 아닙니다.</p></div><div className="adoption-example"><div className="scope-files"><div><code>cart.py</code><span>수정 허용</span></div><div><code>shipping.py</code><span>보호 · 배송비 정책</span></div><div><code>check_discount.py</code><span>보호 · 할인 검사</span></div><div><code>check_shipping.py</code><span>보호 · 배송비 검사</span></div></div><details className="adoption-details"><summary>범위 설정 예시 보기</summary><p>설정 일부이며 완전한 실행 설정이 아닙니다.</p><pre tabIndex={0} role="region" aria-label="작업 범위 설정 일부"><code>{scopeExample}</code></pre></details></div></article></li>
          <li><article className="adoption-step" aria-labelledby="adoption-checks-title"><div className="adoption-copy"><span className="how-icon"><Terminal size={24} aria-hidden="true" /></span><h3 id="adoption-checks-title">2. 검사 명령 연결하기</h3><p>내가 준비한 기존 검사로 완료 기준을 확인합니다. Mercury가 검사를 선택하거나 자동으로 작성하지는 않습니다.</p><p><code>verification.commands</code>에 실행할 명령을 등록하고, <code>criterion_commands</code>에 각 완료 기준과 같은 명령을 연결합니다.</p><p>아래는 신뢰한 Python 실행 파일과 기존 스크립트를 인자 배열로 연결하는 예시입니다. 경로는 내 환경에 맞게 검토해 바꿉니다.</p></div><div className="adoption-example"><div className="criterion-pairs"><div><strong>결제 금액이 음수가 되지 않음</strong><code>check_discount.py</code><span>쿠폰이 상품 금액보다 큰 경우</span></div><div><strong>기존 배송비 정책 유지</strong><code>check_shipping.py</code><span>할인 전 합계에 따른 무료 배송</span></div></div><details className="adoption-details"><summary>검사 연결 설정 예시 보기</summary><p>앞 단계의 완료 기준에 대응하는 설정 일부입니다. 임의의 셸 명령 문자열을 실행하는 형식이 아닙니다.</p><pre tabIndex={0} role="region" aria-label="검사 연결 설정 일부"><code>{checksExample}</code></pre></details></div></article></li>
          <li><article className="adoption-step" aria-labelledby="adoption-run-title"><div className="adoption-copy"><span className="how-icon"><ShieldCheck size={24} aria-hidden="true" /></span><h3 id="adoption-run-title">3. 설정 확인 후 실행하기</h3><p>현재 실행 대상인 macOS에서 Mercury를 설치한 환경과 설치·인증된 에이전트를 준비합니다. 작업 대상은 깨끗한 Git 저장소와 검토한 전체 HEAD로 고정하고, 설정 파일은 대상 저장소와 외부 상태 경로 밖에 둡니다.</p><p>아래 <code>.venv/bin/local-harness</code>는 Mercury를 설치한 환경의 경로입니다. 대상 프로젝트 안에 설치한다는 뜻이 아닙니다. 예시 경로·해시·이름은 실제 검토한 값으로 바꿉니다.</p><a className="text-link" href={`${quickstart}#execute-an-explicit-task`}>완전한 설정과 준비 절차 보기 <ArrowUpRight size={16} aria-hidden="true" /></a></div><div className="adoption-example"><div className="adoption-command"><h4>먼저, 설정 미리보기</h4><pre tabIndex={0} role="region" aria-label="설정 미리보기 명령"><code>{`.venv/bin/local-harness run --preview \\
  --config /absolute/external/run.json`}</code></pre><p>범위·검사·대상 커밋·시간 제한과 설정 해시를 확인합니다. 미리보기는 에이전트나 검사를 실행하지 않으며 승인을 대신하지 않습니다.</p></div><div className="adoption-command"><h4>검토하고 별도 승인한 뒤 실행</h4><pre tabIndex={0} role="region" aria-label="승인 후 실행 명령"><code>{`.venv/bin/local-harness run \\
  --config /absolute/external/run.json \\
  --approve-config-sha256 REVIEWED_64_CHARACTER_SHA256 \\
  --approver YOUR_NAME`}</code></pre><p>검토한 정확한 설정 바이트의 SHA-256을 사용합니다. 설정이 바뀌면 다시 검토·승인하며, 미리보기 해시를 자동으로 실행에 넘기지 않습니다.</p></div></div></article></li>
          <li><article className="adoption-step" aria-labelledby="adoption-results-title"><div className="adoption-copy"><span className="how-icon"><Fingerprint size={24} aria-hidden="true" /></span><h3 id="adoption-results-title">4. 기록으로 다음 행동 판단하기</h3><p>실행 결과의 상태·이유·검사 근거를 함께 읽습니다. 아래 명령에는 상위 state_root가 아니라 실행 결과가 알려준 상태 디렉터리를 사용합니다.</p><ul className="adoption-questions"><li>승인한 범위 안에서 변경됐나요?</li><li>개발 검사와 최종 검사가 각각 실행됐나요?</li><li>실패하거나 확인되지 않은 항목이 있나요?</li></ul><p>필요한 수정·추가 검증·사람 검토를 결정합니다. 선택한 검사의 통과는 모든 동작의 정확성이나 머지 승인을 뜻하지 않습니다.</p></div><div className="adoption-example"><div className="adoption-command"><h4>저장된 실행 기록 읽기</h4><pre tabIndex={0} role="region" aria-label="실행 기록 조회 명령"><code>{`.venv/bin/local-harness status --state-dir /absolute/path/to/run-state --explain`}</code></pre><p>저장된 기록을 읽으며 현재 작업 폴더를 다시 검증하지 않습니다. 조회 명령의 성공은 기록을 읽었다는 뜻이며 작업 성공과 별개입니다.</p></div><div className="adoption-results"><h4>기록에 따라 판단이 어떻게 달라질까요?</h4><p>기존 Mercury 실행 기록을 열어 비교하세요. 수정은 결정적 시뮬레이션이며 실제 AI 성능을 보여주는 자료는 아닙니다.</p><div>{resultLinks.map(link => <a className="text-link" key={link.href} href={link.href}>{link.label} <ArrowUpRight size={15} aria-hidden="true" /></a>)}</div></div></div></article></li>
        </ol>
      </section>
      <section className="start-section" id="get-started" aria-labelledby="start-title"><div className="container"><div className="section-heading"><div><h2 id="start-title">시작 방법을 선택하세요.</h2><p>예제로 흐름을 익히거나, 준비된 내 프로젝트에 적용하세요.</p></div></div><div className="start-grid adoption-start-grid"><div><span className="start-icon"><FlaskConical size={24} aria-hidden="true" /></span><h3>먼저 예제로 이해하기</h3><p>Python 3.12 이상과 Git으로 일회용 예제를 체험합니다. 실제 AI·인증 정보는 필요하지 않습니다.</p><a href={`${quickstart}#recorded-cart-scenarios`} className="text-link">방금 본 장바구니 사례 재현하기 <ArrowUpRight size={17} aria-hidden="true" /></a><a href={quickstart} className="text-link start-secondary">기본 오프라인 quickstart 읽기 <ArrowUpRight size={17} aria-hidden="true" /></a><div className="quickstart-panel"><div className="terminal-heading"><Terminal size={16} aria-hidden="true" /><span>기본 예제 · Mercury 저장소 루트</span></div><pre tabIndex={0} role="region" aria-label="일회용 기본 예제 명령"><code>{`python3.12 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/python examples/quickstart.py --approve-demo`}</code></pre><p><a href={`${github}/blob/main/examples/quickstart.py`}>기본 예제 코드를 먼저 읽으세요.</a> <code>--approve-demo</code>는 임시 저장소의 정해진 수정과 검사를 승인합니다. 내 프로젝트를 실행하는 명령이 아니며, 장바구니는 별도 재현 안내를 따릅니다.</p></div></div><div className="adoption-real-start"><span className="start-icon"><FileCode2 size={24} aria-hidden="true" /></span><h3>내 프로젝트에 적용하기</h3><p>설치된 Mercury와 에이전트, 준비된 검사, 고정한 대상 커밋이 필요합니다. 전체 설정을 준비하고 미리보기·별도 승인을 거쳐 실행합니다.</p><a className="text-link" href={`${quickstart}#execute-an-explicit-task`}>실제 작업 설정 가이드 <ArrowUpRight size={17} aria-hidden="true" /></a><a className="text-link start-secondary" href={`${runGuide}#minimal-execution-and-local-completion`}>실행 결과와 로컬 완료 판단 가이드 <ArrowUpRight size={17} aria-hidden="true" /></a><p className="adoption-note">등록한 검사 외에 필요한 lint·타입·빌드·브라우저 검사는 별도로 계획하고 확인합니다. Mercury의 기술 결과와 사람의 리뷰·머지 승인은 별개입니다.</p></div></div></div></section>
    </main>
    <footer className="site-footer container"><a href="#" className="brand"><span className="brand-mark" aria-hidden="true">m<span /></span>Mercury</a><p>코드 변경 이후, 판단을 위한 근거.</p><div><a href={quickstart}>문서</a><a href={github}>GitHub</a><a href={`${github}/blob/main/LICENSE`}>MIT 라이선스</a></div></footer>
  </>;
}
