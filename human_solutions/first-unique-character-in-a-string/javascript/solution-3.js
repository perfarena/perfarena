function firstUniqueCharIndex(s) {
    const charCount = {};
    
    for (const char of s) {
        charCount[char] = (charCount[char] || 0) + 1;
    }
    
    for (let i = 0; i < s.length; i++) {
        if (charCount[s[i]] === 1) {
            return i;
        }
    }
    
    return -1;
}

// Test the function
const exampleString = "leetcode";
console.log(firstUniqueCharIndex(exampleString)); // Output: 0