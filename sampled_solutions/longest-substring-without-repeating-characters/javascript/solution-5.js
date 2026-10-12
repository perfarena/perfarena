const lengthOfLongestSubstring = (s) => {
    const lastSeen = new Map();
    let startIndex = 0;
    let maxLength = 0;

    for (let i = 0; i < s.length; i++) {
        lastSeen.set(s[i], i);
        maxLength = Math.max(maxLength, i - startIndex + 1);

        if (lastSeen.has(s[i])) {
            startIndex = Math.max(lastSeen.get(s[i]) + 1, startIndex);
        }
    }

    return maxLength;
};