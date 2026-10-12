function lengthOfLongestSubstring(s) {
    let map = {};
    let maxLength = 0;
    let start = 0;

    for (let end = 0; end < s.length; end++) {
        let currentChar = s[end];

        if (map[currentChar] === undefined) {
            map[currentChar] = 0;
        }
        map[currentChar]++;

        while (map[currentChar] > 1) {
            let firstChar = s[start];
            map[firstChar]--;
            start++;
        }

        maxLength = Math.max(maxLength, end - start + 1);
    }

    return maxLength;
}