document.addEventListener('DOMContentLoaded', function() {
    // Initialize the config page
    initConfigPage();
    
    // Periodically check announcement status
    setInterval(checkAnnouncementStatus, 1000);
});

function initConfigPage() {
    console.log('Configuration page loaded.');
    
    // Setup test buttons
    setupTestButtons();
    
    // Initialize form validation
    setupFormValidation();
    
    // Setup flash message auto-hide
    setupFlashMessages();
}

function setupTestButtons() {
    // Setup button event listeners
    document.getElementById('test-button1')?.addEventListener('click', function() {
        const text = document.getElementById('button1').value;
        testAnnouncement(text, 'Button 1');
    });
    
    document.getElementById('test-button2')?.addEventListener('click', function() {
        const text = document.getElementById('button2').value;
        testAnnouncement(text, 'Button 2');
    });
    
    document.getElementById('test-button3')?.addEventListener('click', function() {
        const text = document.getElementById('button3').value;
        testAnnouncement(text, 'Button 3');
    });
}

function testAnnouncement(text, buttonName) {
    // Show loading indicator
    const statusMessage = document.getElementById('test-status');
    if (statusMessage) {
        statusMessage.textContent = `Playing announcement for ${buttonName}...`;
        statusMessage.className = 'status-message';
    }
    
    // Disable test buttons while playing
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
            // Handle cooldown period case
            return response.json().then(data => {
                throw new Error('Please wait for the current announcement to finish');
            });
        }
        
        if (!response.ok) {
            return response.json().then(data => {
                throw new Error(data.error || 'Failed to play announcement');
            });
        }
        return response.json();
    })
    .then(data => {
        if (statusMessage) {
            statusMessage.textContent = 'Announcement played successfully';
            statusMessage.className = 'status-message success';
            
            // Auto-hide success message after 3 seconds
            setTimeout(() => {
                statusMessage.textContent = '';
                statusMessage.className = 'status-message';
            }, 3000);
        }
    })
    .catch(error => {
        console.error('Error:', error);
        if (statusMessage) {
            statusMessage.textContent = error.message || 'Error playing announcement';
            statusMessage.className = 'status-message error';
        }
    });
}

function checkAnnouncementStatus() {
    fetch('/announcement_status')
        .then(response => response.json())
        .then(data => {
            // Update UI based on announcement status
            const testButtons = document.querySelectorAll('.test-buttons button');
            
            if (data.is_playing) {
                // Announcement is playing - disable buttons
                testButtons.forEach(btn => btn.disabled = true);
                
                // Show message if no message is currently displayed
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
                // No announcement playing - enable buttons
                testButtons.forEach(btn => btn.disabled = false);
                
                // Clear the status message if it's showing the "please wait" message
                const statusMessage = document.getElementById('test-status');
                if (statusMessage && (statusMessage.textContent.includes('Please wait') || 
                                      statusMessage.textContent.includes('announcement is currently playing'))) {
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
            // Simple validation for required fields
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
    // Auto-hide flash messages after 5 seconds
    const flashMessages = document.querySelectorAll('.alert');
    flashMessages.forEach(message => {
        setTimeout(() => {
            message.style.opacity = '0';
            setTimeout(() => message.remove(), 500);
        }, 5000);
    });
}
