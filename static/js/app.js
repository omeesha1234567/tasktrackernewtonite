// Newtonite Task Tracker - Client-side Utilities & Micro-interactions
(function () {
  'use strict';

  // 1. Theme Management (Light / Dark)
  function initTheme() {
    var storedTheme = localStorage.getItem('newtonite-theme');
    var currentTheme = storedTheme;
    if (!currentTheme) {
      if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) {
        currentTheme = 'dark';
      } else {
        currentTheme = 'light';
      }
    }
    applyTheme(currentTheme, false);

    // Listen to OS theme changes if user hasn't explicitly set a preference
    if (window.matchMedia) {
      window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', function (e) {
        if (!localStorage.getItem('newtonite-theme')) {
          applyTheme(e.matches ? 'dark' : 'light', false);
        }
      });
    }
  }

  function applyTheme(theme, save) {
    document.documentElement.setAttribute('data-theme', theme);
    if (save) {
      localStorage.setItem('newtonite-theme', theme);
    }
    updateThemeIcons(theme);
    
    // Dispatch custom event for charts or dynamic components
    window.dispatchEvent(new CustomEvent('newtoniteThemeChanged', { detail: { theme: theme } }));
  }

  function updateThemeIcons(theme) {
    var sunIcons = document.querySelectorAll('.theme-icon-sun');
    var moonIcons = document.querySelectorAll('.theme-icon-moon');
    if (theme === 'dark') {
      sunIcons.forEach(function (el) { el.style.display = 'inline-block'; });
      moonIcons.forEach(function (el) { el.style.display = 'none'; });
    } else {
      sunIcons.forEach(function (el) { el.style.display = 'none'; });
      moonIcons.forEach(function (el) { el.style.display = 'inline-block'; });
    }
  }

  window.toggleTheme = function () {
    var currentTheme = document.documentElement.getAttribute('data-theme') || 'light';
    var nextTheme = (currentTheme === 'dark') ? 'light' : 'dark';
    applyTheme(nextTheme, true);
  };

  // Run theme init immediately to avoid flash
  initTheme();

  // 2. DOMContentLoaded initializers
  document.addEventListener('DOMContentLoaded', function () {
    // Theme toggle button click
    var themeToggleBtn = document.getElementById('theme-toggle-btn');
    if (themeToggleBtn) {
      themeToggleBtn.addEventListener('click', function (e) {
        e.preventDefault();
        window.toggleTheme();
      });
      // Ensure icons reflect active theme on DOM load
      var currentTheme = document.documentElement.getAttribute('data-theme') || 'light';
      updateThemeIcons(currentTheme);
    }

    // Mobile sidebar toggle
    var mobileToggle = document.getElementById('mobile-sidebar-toggle');
    var sidebar = document.getElementById('app-sidebar');
    if (mobileToggle && sidebar) {
      mobileToggle.addEventListener('click', function () {
        sidebar.classList.toggle('open');
      });
    }

    // Auto-dismiss alerts after 6 seconds with smooth fade
    var alerts = document.querySelectorAll('.alert');
    alerts.forEach(function (alert) {
      setTimeout(function () {
        alert.style.opacity = '0';
        alert.style.transform = 'translateY(-10px)';
        alert.style.transition = 'opacity 0.4s ease, transform 0.4s ease';
        setTimeout(function () {
          if (alert.parentNode) {
            alert.remove();
          }
        }, 400);
      }, 6000);
    });

    // Password visibility toggle
    var togglePassBtns = document.querySelectorAll('[data-toggle-password]');
    togglePassBtns.forEach(function (btn) {
      btn.addEventListener('click', function () {
        var targetId = btn.getAttribute('data-toggle-password');
        var input = document.getElementById(targetId);
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

    // Prevent double form submissions on action forms
    var forms = document.querySelectorAll('form:not([data-no-submit-lock])');
    forms.forEach(function (form) {
      form.addEventListener('submit', function () {
        var submitBtn = form.querySelector('button[type="submit"]:not([data-no-disable])');
        if (submitBtn && !submitBtn.disabled) {
          submitBtn.setAttribute('data-original-html', submitBtn.innerHTML);
          submitBtn.disabled = true;
          submitBtn.classList.add('btn-loading');
          
          // Add a subtle spinner or loading text
          var loadingText = submitBtn.getAttribute('data-loading-text') || 'Processing...';
          submitBtn.innerHTML = '<span class="spinner-sm"></span> ' + loadingText;
          
          // Re-enable after 10 seconds timeout in case of client-side abort or unnavigated response
          setTimeout(function () {
            if (submitBtn.disabled) {
              submitBtn.disabled = false;
              submitBtn.classList.remove('btn-loading');
              var orig = submitBtn.getAttribute('data-original-html');
              if (orig) submitBtn.innerHTML = orig;
            }
          }, 10000);
        }
      });
    });

    // Dynamic Assignee dropdown update based on selected Team in Task Create
    var teamSelect = document.getElementById('task-team-select');
    var assigneeSelect = document.getElementById('task-assignee-select');

    if (teamSelect && assigneeSelect) {
      teamSelect.addEventListener('change', function () {
        var teamId = this.value;
        if (!teamId) {
          assigneeSelect.innerHTML = '<option value="">Unassigned</option>';
          return;
        }

        fetch('/api/teams/' + encodeURIComponent(teamId) + '/members/')
          .then(function (response) {
            if (!response.ok) throw new Error('Failed to fetch members');
            return response.json();
          })
          .then(function (data) {
            var currentVal = assigneeSelect.value;
            assigneeSelect.innerHTML = '<option value="">Unassigned</option>';
            data.members.forEach(function (member) {
              var opt = document.createElement('option');
              opt.value = member.id;
              opt.textContent = member.full_name + ' (@' + member.username + ')';
              if (String(member.id) === String(currentVal)) {
                opt.selected = true;
              }
              assigneeSelect.appendChild(opt);
            });
          })
          .catch(function (err) {
            console.warn('Assignee fetch warning:', err);
          });
      });
    }

    // 3. Task Attachment Drag-and-Drop and File Selection Handling
    var dropzone = document.getElementById('file-dropzone');
    var fileInput = document.getElementById('id_attachment_file') || document.getElementById('file-upload-input');
    var selectedFileDisplay = document.getElementById('selected-file-display');
    var fileNameSpan = document.getElementById('selected-file-name');
    var fileSizeSpan = document.getElementById('selected-file-size');

    if (dropzone && fileInput) {
      // Highlight on drag over
      ['dragenter', 'dragover'].forEach(function (eventName) {
        dropzone.addEventListener(eventName, function (e) {
          e.preventDefault();
          e.stopPropagation();
          dropzone.classList.add('drag-over');
        }, false);
      });

      ['dragleave', 'drop'].forEach(function (eventName) {
        dropzone.addEventListener(eventName, function (e) {
          e.preventDefault();
          e.stopPropagation();
          dropzone.classList.remove('drag-over');
        }, false);
      });

      // Handle file dropped
      dropzone.addEventListener('drop', function (e) {
        var dt = e.dataTransfer;
        var files = dt.files;
        if (files && files.length > 0) {
          fileInput.files = files;
          updateSelectedFilePreview(files[0]);
        }
      });

      // Handle file chosen via input
      fileInput.addEventListener('change', function () {
        if (fileInput.files && fileInput.files.length > 0) {
          updateSelectedFilePreview(fileInput.files[0]);
        } else {
          clearSelectedFilePreview();
        }
      });
    }

    function formatBytes(bytes) {
      if (bytes === 0) return '0 Bytes';
      var k = 1024;
      var sizes = ['Bytes', 'KB', 'MB', 'GB'];
      var i = Math.floor(Math.log(bytes) / Math.log(k));
      return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
    }

    function updateSelectedFilePreview(file) {
      if (selectedFileDisplay && fileNameSpan) {
        fileNameSpan.textContent = file.name;
        if (fileSizeSpan) {
          fileSizeSpan.textContent = '(' + formatBytes(file.size) + ')';
        }
        selectedFileDisplay.style.display = 'flex';
      }
    }

    function clearSelectedFilePreview() {
      if (selectedFileDisplay) {
        selectedFileDisplay.style.display = 'none';
      }
    }
  });

  // Modal helpers (global)
  window.openModal = function (modalId) {
    var modal = document.getElementById(modalId);
    if (modal) {
      modal.classList.add('active');
    }
  };

  window.closeModal = function (modalId) {
    var modal = document.getElementById(modalId);
    if (modal) {
      modal.classList.remove('active');
    }
  };

  window.closeAlert = function (button) {
    var alert = button.closest('.alert');
    if (alert) {
      alert.style.opacity = '0';
      alert.style.transform = 'translateY(-10px)';
      alert.style.transition = 'opacity 0.25s ease, transform 0.25s ease';
      setTimeout(function () {
        alert.remove();
      }, 250);
    }
  };
})();
