// Newtonite Task Tracker - Client-side Utilities

document.addEventListener('DOMContentLoaded', function () {
  // Mobile sidebar toggle
  const mobileToggle = document.getElementById('mobile-sidebar-toggle');
  const sidebar = document.getElementById('app-sidebar');
  if (mobileToggle && sidebar) {
    mobileToggle.addEventListener('click', function () {
      sidebar.classList.toggle('open');
    });
  }

  // Auto-dismiss alerts after 5 seconds
  const alerts = document.querySelectorAll('.alert');
  alerts.forEach(function (alert) {
    setTimeout(function () {
      alert.style.opacity = '0';
      alert.style.transition = 'opacity 0.4s ease';
      setTimeout(function () {
        alert.remove();
      }, 400);
    }, 5000);
  });

  // Password visibility toggle
  const togglePassBtns = document.querySelectorAll('[data-toggle-password]');
  togglePassBtns.forEach(function (btn) {
    btn.addEventListener('click', function () {
      const targetId = btn.getAttribute('data-toggle-password');
      const input = document.getElementById(targetId);
      if (input) {
        if (input.type === 'password') {
          input.type = 'text';
          btn.textContent = 'Hide';
        } else {
          input.type = 'password';
          btn.textContent = 'Show';
        }
      }
    });
  });

  // Dynamic Assignee dropdown update based on selected Team in Task Create
  const teamSelect = document.getElementById('task-team-select');
  const assigneeSelect = document.getElementById('task-assignee-select');

  if (teamSelect && assigneeSelect) {
    teamSelect.addEventListener('change', function () {
      const teamId = this.value;
      if (!teamId) {
        assigneeSelect.innerHTML = '<option value="">Unassigned</option>';
        return;
      }

      fetch(`/api/teams/${teamId}/members/`)
        .then(response => {
          if (!response.ok) throw new Error('Failed to fetch members');
          return response.json();
        })
        .then(data => {
          const currentVal = assigneeSelect.value;
          assigneeSelect.innerHTML = '<option value="">Unassigned</option>';
          data.members.forEach(member => {
            const opt = document.createElement('option');
            opt.value = member.id;
            opt.textContent = `${member.full_name} (@${member.username})`;
            if (String(member.id) === String(currentVal)) {
              opt.selected = true;
            }
            assigneeSelect.appendChild(opt);
          });
        })
        .catch(err => {
          console.warn('Assignee fetch warning:', err);
        });
    });
  }
});

// Modal helpers
function openModal(modalId) {
  const modal = document.getElementById(modalId);
  if (modal) {
    modal.classList.add('active');
  }
}

function closeModal(modalId) {
  const modal = document.getElementById(modalId);
  if (modal) {
    modal.classList.remove('active');
  }
}

function closeAlert(button) {
  const alert = button.closest('.alert');
  if (alert) {
    alert.remove();
  }
}
