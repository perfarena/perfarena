function check(s) {
    let left = 0;
    let right = s.length - 1;
    
    while (left <= right) {
        if (s[left] === s[right]) {
            left++;
            right--;
        } else {
            return false;
        }
    }
    
    return true;
}

function clean(s) {
    return s.replace(/[^a-zA-Z0-9]/g, '').toLowerCase();
}

// Test the algorithm
const input = "I'm at a planet Canal Panama";
const cleanedInput = clean(input);
const isPalindrome = check(cleanedInput);
console.log(isPalindrome);