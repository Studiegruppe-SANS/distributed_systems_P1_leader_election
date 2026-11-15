// henter en ny cookie fra lederen. Under et lederskifte svarer servicen 503 eller slet ikke,
// så der prøves igen indtil en ny leder er valgt

const fortuneElement = document.getElementById("fortune");
const metaElement = document.getElementById("meta");
const button = document.getElementById("more");
const cookieElelement = document.querySelector(".cookie");

const RETRY_DELAY_MS = 300;
const GIVE_UP_AFTER_MS = 30000;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// This function fetches the cookie result and returns if valid
async function fetchCookie() {
  const result = await fetch("/api/cookie", { cache: "no-store" });
  if (!result.ok) {
    throw new Error(`HTTP ${result.status}`);
  }
  return result.json();
}

async function getCookie() {
  button.disabled = true;
  cookieElelement.classList.remove("open");
  const started = performance.now();
  let attempts = 0;

  while (true) {
    attempts += 1;
    try {
      const data = await fetchCookie();
      const ms = Math.round(performance.now() - started);

      fortuneElement.classList.remove("error");
      fortuneElement.textContent = data.fortune;
      cookieElelement.classList.add("open");

      if (attempts > 1) {
        metaElement.textContent = `served by leader ${data.pod} · ${ms} ms · ${attempts - 1} retries during leader election`
      } else {
        metaElement.textContent = `served by leader ${data.pod} · ${ms} ms`
      }

      break;
    } catch (error) {
      const waited = performance.now() - started;
      if (waited > GIVE_UP_AFTER_MS) {
        fortuneElement.classList.add("error");
        fortuneElement.textContent = "No leader available right now. Please try again.";
        metaElement.textContent = `${error.message} after ${attempts} attempts`;
        break;
      }
      metaElement.textContent = `waiting for a leader… (${(waited / 1000).toFixed(1)} s)`;
      await sleep(RETRY_DELAY_MS);
    }
  }
  
  button.disabled = false;
}

button.addEventListener("click", getCookie);
getCookie();
