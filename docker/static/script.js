// henter en ny cookie fra lederen. Under et lederskifte svarer servicen 503 eller slet ikke,
// så der prøves igen indtil en ny leder er valgt

const fortuneEl = document.getElementById("fortune");
const metaEl = document.getElementById("meta");
const button = document.getElementById("more");
const cookieEl = document.querySelector(".cookie");

const RETRY_DELAY_MS = 300;
const GIVE_UP_AFTER_MS = 30000;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function fetchCookie() {
  const res = await fetch("/api/cookie", { cache: "no-store" });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function getCookie() {
  button.disabled = true;
  cookieEl.classList.remove("open");
  const started = performance.now();
  let attempts = 0;

  while (true) {
    attempts += 1;
    try {
      const data = await fetchCookie();
      const ms = Math.round(performance.now() - started);
      fortuneEl.classList.remove("error");
      fortuneEl.textContent = data.fortune;
      cookieEl.classList.add("open");
      metaEl.textContent = `served by leader ${data.pod} · ${ms} ms` +
        (attempts > 1 ? ` · ${attempts - 1} retries during leader election` : "");
      break;
    } catch (err) {
      const waited = performance.now() - started;
      if (waited > GIVE_UP_AFTER_MS) {
        fortuneEl.classList.add("error");
        fortuneEl.textContent = "No leader available right now. Please try again.";
        metaEl.textContent = `${err.message} after ${attempts} attempts`;
        break;
      }
      metaEl.textContent = `waiting for a leader… (${(waited / 1000).toFixed(1)} s)`;
      await sleep(RETRY_DELAY_MS);
    }
  }
  button.disabled = false;
}

button.addEventListener("click", getCookie);
getCookie();
