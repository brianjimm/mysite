
// Flash message auto-hide functionality
document.addEventListener('DOMContentLoaded', function() {
    // Auto-hide flash messages after 5 seconds
    const flashMessages = document.querySelectorAll('.flash-message');
    flashMessages.forEach(function(message) {
        setTimeout(function() {
            message.style.transition = 'opacity 0.5s ease-out';
            message.style.opacity = '0';
            setTimeout(function() {
                message.remove();
            }, 500);
        }, 5000);
    });

    // Close button functionality
    const closeButtons = document.querySelectorAll('.flash-close');
    closeButtons.forEach(function(button) {
        button.addEventListener('click', function() {
            const message = this.parentElement;
            message.style.transition = 'opacity 0.3s ease-out';
            message.style.opacity = '0';
            setTimeout(function() {
                message.remove();
            }, 300);
        });
    });
});

