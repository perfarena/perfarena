let left = 0;
let longest = 0;
const charMap = new Map();

for (let right = 0; right < s.length; right++) {
    const currentChar = s[right];
    const previousCharIndex = charMap.get(currentChar);

    if (previousCharIndex >= left) {
        left = previousCharIndex + 1;
    }

    charMap.set(currentChar, right);
    
    const currentLength = right - left + 1;
    longest = Math.max(longest, currentLength);
}

return longest;