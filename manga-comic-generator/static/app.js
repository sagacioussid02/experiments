const charactersEl = document.getElementById("characters");
const template = document.getElementById("character-template");
const statusEl = document.getElementById("status");
const pagesEl = document.getElementById("pages");

function addCharacter() {
  const node = template.content.cloneNode(true);
  node.querySelector(".remove").addEventListener("click", (event) => {
    event.target.closest("fieldset").remove();
  });
  charactersEl.appendChild(node);
}

document.getElementById("add-character").addEventListener("click", addCharacter);
addCharacter();
addCharacter();

document.getElementById("comic-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const characterEls = [...document.querySelectorAll("fieldset.character")];
  if (characterEls.length === 0) {
    statusEl.textContent = "Add at least one character first.";
    return;
  }

  const formData = new FormData();
  characterEls.forEach((el) => {
    formData.append("names", el.querySelector(".name").value);
    formData.append("backstories", el.querySelector(".backstory").value);
    formData.append("personalities", el.querySelector(".personality").value);
    formData.append("visual_descriptions", el.querySelector(".visual").value);
    const file = el.querySelector(".image").files[0];
    formData.append("images", file || new File([], ""));
  });
  const theme = document.querySelector("input[name=theme]").value;
  if (theme) formData.append("theme", theme);

  pagesEl.innerHTML = "";
  statusEl.textContent = "Creating project...";

  const createResponse = await fetch("/projects", { method: "POST", body: formData });
  if (!createResponse.ok) {
    statusEl.textContent = `Failed to create project: ${await createResponse.text()}`;
    return;
  }
  const project = await createResponse.json();

  statusEl.textContent = "Generating story, script, panels, and layout (this can take a minute)...";
  const runResponse = await fetch(`/projects/${project.id}/run`, { method: "POST" });
  if (!runResponse.ok) {
    statusEl.textContent = `Generation failed: ${await runResponse.text()}`;
    return;
  }
  const finished = await runResponse.json();

  statusEl.textContent = `Done: "${finished.story.title}" (${finished.pages.length} pages).`;
  finished.pages.forEach((page) => {
    const img = document.createElement("img");
    img.src = `/projects/${finished.id}/pages/${page.page_number}`;
    pagesEl.appendChild(img);
  });

  const link = document.createElement("a");
  link.href = `/projects/${finished.id}/pdf`;
  link.textContent = "Download PDF";
  pagesEl.appendChild(link);
});
