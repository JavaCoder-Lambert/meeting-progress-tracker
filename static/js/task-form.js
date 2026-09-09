(() => {
  const form = document.querySelector('[data-task-form]');
  const data = document.getElementById('task-phase-options');
  if (!form || !data) return;
  const phases = JSON.parse(data.textContent);
  const project = form.querySelector('[name="project"]');
  const phase = form.querySelector('[name="phase"]');
  const status = form.querySelector('[data-task-phase-status]');

  function updatePhases() {
    const previous = phase.value;
    const available = phases.filter(item => String(item.project_id) === project.value);
    const options = [{id: '', name: '未归属阶段'}, ...available].map(item => {
      const option = document.createElement('option');
      option.value = String(item.id);
      option.textContent = item.name;
      return option;
    });
    phase.replaceChildren(...options);
    phase.value = available.some(item => String(item.id) === previous) ? previous : '';
    status.textContent = previous && !phase.value
      ? '原阶段不属于当前项目，已清空，请按需重新选择。'
      : project.value ? (available.length ? '仅显示所选项目的阶段。' : '这个项目暂无阶段，可先保存任务。') : '请先选择所属项目。';
  }

  project.addEventListener('change', updatePhases);
  updatePhases();
})();
