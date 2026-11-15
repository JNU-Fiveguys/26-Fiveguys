/* 추천 계산 중 로딩 화면 — 김 나는 밥그릇 위로 음식이 차례로 튀어 오르고, 진행 문구가 바뀐다.
 *  window.MealLoader.show(box)  : box 안을 로딩 화면으로 바꾼다
 *  window.MealLoader.stop()     : 문구 타이머 정리 (box 내용은 호출한 쪽이 바꾼다)
 *  움직임 줄이기 설정이면 CSS 가 애니메이션을 끈다.
 */
(() => {
  const FOODS = ['🍜', '🍣', '🍙', '🥘', '🍔', '🥟'];
  const STEPS = ['보행 경로를 계산하고 있어요', '영업시간을 확인하고 있어요', '맛있는 곳을 고르고 있어요'];
  let timer = null;

  const BOWL = `
<svg class="ml-bowl" viewBox="0 0 120 96" aria-hidden="true">
  <g class="ml-steam" fill="none" stroke-linecap="round" stroke-width="3">
    <path class="s1" d="M44 34c-6-7 6-11 0-18"/>
    <path class="s2" d="M60 32c-6-7 6-11 0-18"/>
    <path class="s3" d="M76 34c-6-7 6-11 0-18"/>
  </g>
  <ellipse class="ml-rice" cx="60" cy="52" rx="38" ry="12"/>
  <path class="ml-rice-top" d="M26 52c4-12 18-17 34-17s30 5 34 17z"/>
  <path class="ml-cup" d="M18 52h84c0 22-18 36-42 36S18 74 18 52z"/>
  <path class="ml-band" d="M24 64h72"/>
  <rect class="ml-foot" x="46" y="86" width="28" height="6" rx="3"/>
</svg>`;

  function show(box) {
    stop();
    box.className = 'empty meal-loader';
    box.setAttribute('aria-busy', 'true');
    const stage = document.createElement('div');
    stage.className = 'ml-stage';
    stage.innerHTML = BOWL;
    const foods = document.createElement('div');
    foods.className = 'ml-foods';
    foods.setAttribute('aria-hidden', 'true');
    FOODS.forEach((f, i) => {
      const span = document.createElement('span');
      span.textContent = f;
      span.style.animationDelay = `${i * 0.6}s`;
      foods.appendChild(span);
    });
    stage.prepend(foods);

    const text = document.createElement('p');
    text.className = 'ml-text';
    text.setAttribute('role', 'status');
    let step = 0;
    const label = document.createElement('span');
    label.textContent = STEPS[0];
    const dots = document.createElement('span');
    dots.className = 'ml-dots';
    dots.setAttribute('aria-hidden', 'true');
    dots.innerHTML = '<i>.</i><i>.</i><i>.</i>';
    text.append(label, dots);

    box.replaceChildren(stage, text);
    timer = setInterval(() => {
      step = (step + 1) % STEPS.length;
      label.textContent = STEPS[step];
    }, 1800);
  }

  function stop() {
    clearInterval(timer);
    timer = null;
  }

  window.MealLoader = { show, stop };
})();
