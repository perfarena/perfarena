function lengthOfLongestSubstring(s) {
    let max = 0;
    let windowStart = 0;
    const soFar = {};

    for (let windowEnd = 0; windowEnd < s.length; windowEnd++) {
        const rightChar = s[windowEnd];
        soFar[rightChar] = soFar[rightChar] ? soFar[rightChar] + 1 : 1;

        while (soFar[rightChar] > 1) {
            const leftChar = s[windowStart];
            if (soFar[leftChar] > 1) {
                soFar[leftChar]--;
            } else {
                delete soFar[leftChar];
            }
            windowStart++;
        }

        max = Math.max(max, windowEnd - windowStart + 1);
    }

    return max;
}