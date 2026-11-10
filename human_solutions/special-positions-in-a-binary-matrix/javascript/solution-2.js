function areStringHalvesAlike(s) {
    const vowels = { 'a': true, 'e': true, 'i': true, 'o': true, 'u': true };
    s = s.toLowerCase();
    let count = 0;

    for (let i = 0; i < s.length / 2; i++) {
        if (vowels[s[i]]) {
            count++;
        }
    }

    for (let i = s.length / 2; i < s.length; i++) {
        if (vowels[s[i]]) {
            count--;
        }
    }

    return count === 0;
}

// Test the function
console.log(areStringHalvesAlike("book")); // Output: true
console.log(areStringHalvesAlike("hello")); // Output: false