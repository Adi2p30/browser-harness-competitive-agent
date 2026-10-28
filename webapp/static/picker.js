// Turns a <fieldset class="picker" data-name="..."> into a dropdown-to-add chip list, with an
// optional freewrite input/button for values not in the dropdown. Each chip carries a hidden
// <input name="{data-name}"> so the surrounding <form> submits one value per chip, same as the
// repeated checkboxes/fields it replaces.
(function () {
  function initPicker(container) {
    const name = container.dataset.name;
    const select = container.querySelector(".picker-select");
    const freewrite = container.querySelector(".picker-freewrite");
    const addButton = container.querySelector(".picker-add");
    const chips = container.querySelector(".chips");
    const seen = new Set();

    function addChip(value) {
      value = value.trim();
      if (!value || seen.has(value)) return;
      seen.add(value);

      const hidden = document.createElement("input");
      hidden.type = "hidden";
      hidden.name = name;
      hidden.value = value;

      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "chip-remove";
      remove.textContent = "×";
      remove.setAttribute("aria-label", "Remove " + value);

      const chip = document.createElement("span");
      chip.className = "chip";
      chip.append(document.createTextNode(value), hidden, remove);
      remove.addEventListener("click", () => {
        seen.delete(value);
        chip.remove();
      });

      chips.appendChild(chip);
    }

    if (select) {
      select.addEventListener("change", () => {
        if (select.value) {
          addChip(select.value);
          select.value = "";
        }
      });
    }
    if (freewrite) {
      const commit = () => {
        addChip(freewrite.value);
        freewrite.value = "";
      };
      if (addButton) addButton.addEventListener("click", commit);
      freewrite.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          commit();
        }
      });
    }
  }

  document.querySelectorAll(".picker").forEach(initPicker);
})();
