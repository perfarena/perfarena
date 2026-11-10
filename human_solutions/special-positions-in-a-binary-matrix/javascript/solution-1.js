function halvesAreAlike(s) {
    const vowels = 'aeiouAEIOU';
    const len = s.length;
    const a = s.substring(0, Math.floor(len / 2));
    const b = s.substring(Math.ceil(len / 2));

    let aVowelCount = 0;
    let bVowelCount = 0;

    for (let i = 0; i < a.length; i++) {
        if (vowels.includes(a[i])) {
            aVowelCount++;
        }
    }

    for (let i = 0; i < b.length; i++) {
        if (vowels.includes(b[i])) {
            bVowelCount++;
        }
    }

    return aVowelCount === bVowelCount;
}