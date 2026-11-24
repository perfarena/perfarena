let wordCount = 0;
let result = '';

for (let i = 0; i < s.length; i++) {
    if (s[i] === ' ') {
        wordCount++;
        if (wordCount === k) {
            break;
        }
    }
}

if (wordCount !== k) {
    i++;
}

result = s.slice(0, i);

return result;