/*
Written by Seth Morrow

This JavaScript file manages the client-side behavior for the Go-Kart Announcement System UI.
It handles test button events, including a new event for playing the Yiddish announcement.
It also periodically checks the status of announcements.
*/

document.addEventListener('DOMContentLoaded', function() {
    // Initialize configuration page
    initConfigPage();
    // Periodically check announcement status every second
    setInterval(checkAnnouncementStatus, 1000);
});

function initConfigPage() {
    // Log initialization
    console.log('Configuration page loaded.');
    // Setup test buttons
    setupTestButtons();
    // Initialize form validation
    setupFormValidation();
    // Setup flash message auto-hide
    setupFlashMessages();
}

function setupTestButtons() {
    // Add event listener for Button 1
    document.getElementById('test-button1')?.addEventListener('click', function() {
        const text = document.getElementById('button1').value;
        testAnnouncement(text, 'Button 1');
    });
    // Add event listener for Button 2
    document.getElementById('test-button2')?.addEventListener('click', function() {
        const text = document.getElementById('button2').value;
        testAnnouncement(text, 'Button 2');
    });
    // Add event listener for Button 3
    document.getElementById('test-button3')?.addEventListener('click', function() {
        const text = document.getElementById('button3').value;
        testAnnouncement(text, 'Button 3');
    });
    // Add event listener for the new Yiddish announcement button
    document.getElementById('test-yiddish')?.addEventListener('click', function() {
        const statusMessage = document.getElementById('test-status');
        if (statusMessage) {
            statusMessage.textContent = 'Playing Yiddish announcement...';
            statusMessage.className = 'status-message';
        }
        // Disable all test buttons during playback
        const testButtons = document.querySelectorAll('.test-buttons button');
        testButtons.forEach(btn => btn.disabled = true);
        // Send POST request to play the Yiddish announcement
        fetch('/play_yiddish', { method: 'POST' })
        .then(response => {
            if (!response.ok) {
                return response.json().then(data => { throw new Error(data.error || 'Failed to play Yiddish announcement'); });
            }
            return response.json();
        })
        .then(data => {
            if (statusMessage) {
                statusMessage.textContent = 'Yiddish announcement played successfully';
                statusMessage.className = 'status-message success';
                setTimeout(() => {
                    statusMessage.textContent = '';
                    statusMessage.className = 'status-message';
                }, 3000);
            }
        })
        .catch(error => {
            if (statusMessage) {
                statusMessage.textContent = error.message || 'Error playing Yiddish announcement';
                statusMessage.className = 'status-message error';
            }
        })
        .finally(() => {
            testButtons.forEach(btn => btn.disabled = false);
        });
    });
}

function testAnnouncement(text, buttonName) {
    const statusMessage = document.getElementById('test-status');
    if (statusMessage) {
        statusMessage.textContent = `Playing announcement for ${buttonName}...`;
        statusMessage.className = 'status-message';
    }
    const testButtons = document.querySelectorAll('.test-buttons button');
    testButtons.forEach(btn => btn.disabled = true);
    fetch('/play_instant', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({ text: text })
    })
    .then(response => {
        if (response.status === 429) {
            return response.json().then(data => { throw new Error('Please wait for the current announcement to finish'); });
        }
        if (!response.ok) {
            return response.json().then(data => { throw new Error(data.error || 'Failed to play announcement, please save any changes.'); });
        }
        return response.json();
    })
    .then(data => {
        if (statusMessage) {
            statusMessage.textContent = 'Announcement played successfully';
            statusMessage.className = 'status-message success';
            setTimeout(() => {
                statusMessage.textContent = '';
                statusMessage.className = 'status-message';
            }, 3000);
        }
    })
    .catch(error => {
        if (statusMessage) {
            statusMessage.textContent = error.message || 'Error playing announcement';
            statusMessage.className = 'status-message error';
        }
    })
    .finally(() => {
        testButtons.forEach(btn => btn.disabled = false);
    });
}

function checkAnnouncementStatus() {
    fetch('/announcement_status')
    .then(response => response.json())
    .then(data => {
        const testButtons = document.querySelectorAll('.test-buttons button');
        if (data.is_playing) {
            testButtons.forEach(btn => btn.disabled = true);
            const statusMessage = document.getElementById('test-status');
            if (statusMessage && !statusMessage.textContent) {
                if (data.seconds_remaining > 0) {
                    statusMessage.textContent = `Please wait ${Math.ceil(data.seconds_remaining)} seconds before playing another announcement...`;
                } else {
                    statusMessage.textContent = 'An announcement is currently playing...';
                }
                statusMessage.className = 'status-message';
            }
        } else {
            testButtons.forEach(btn => btn.disabled = false);
            const statusMessage = document.getElementById('test-status');
            if (statusMessage && (statusMessage.textContent.includes('Please wait') || statusMessage.textContent.includes('announcement is currently playing'))) {
                statusMessage.textContent = '';
                statusMessage.className = 'status-message';
            }
        }
    })
    .catch(error => {
        console.error('Error checking announcement status:', error);
    });
}

function setupFormValidation() {
    const form = document.getElementById('configForm');
    if (form) {
        form.addEventListener('submit', function(event) {
            const requiredInputs = form.querySelectorAll('[required]');
            let valid = true;
            requiredInputs.forEach(input => {
                if (!input.value.trim()) {
                    valid = false;
                    input.classList.add('error');
                } else {
                    input.classList.remove('error');
                }
            });
            if (!valid) {
                event.preventDefault();
                alert('Please fill in all required fields');
            }
        });
    }
}

function setupFlashMessages() {
    const flashMessages = document.querySelectorAll('.alert');
    flashMessages.forEach(message => {
        setTimeout(() => {
            message.style.opacity = '0';
            setTimeout(() => message.remove(), 500);
        }, 5000);
    });
}
