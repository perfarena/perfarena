function firstNonRepeatingIndex(str) {
    const charCount = {};
    
    for (let i = 0; i < str.length; i++) {
        const char = str[i];
        charCount[char] = charCount[char] ? charCount[char] + 1 : 1;
    }
    
    for (let i = 0; i < str.length; i++) {
        if (charCount[str[i]] === 1) {
            return i;
        }
    }
    
    return -1;
}

// Test the function
console.log(firstNonRepeatingIndex("leetcode")); // Output: 0
console.log(firstNonRepeatingIndex("loveleetcode")); // Output: 2