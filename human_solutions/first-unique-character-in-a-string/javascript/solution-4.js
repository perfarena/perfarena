function firstUniqueChar(s) {
    const frequencies = {}; // Map to store character frequencies
    let result = -1; // Initialize result index to -1

    // Update character frequencies in the map
    for (let i = 0; i < s.length; i++) {
        const char = s[i];
        if (frequencies[char] === undefined) {
            frequencies[char] = 1;
        } else {
            frequencies[char]++;
        }
    }

    // Find the first unique character
    for (let i = 0; i < s.length; i++) {
        const char = s[i];
        if (frequencies[char] === 1) {
            result = i;
            break;
        }
    }

    return result;
}

// Example usage
console.log(firstUniqueChar("leetcode")); // Output: 0