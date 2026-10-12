function canConstructRansomNote(ransomNote, magazine) {
    if (ransomNote.length > magazine.length) {
        return false; // Ransom note cannot be constructed if it's longer than the magazine
    }

    const magazineTable = {};
    for (let char of magazine) {
        magazineTable[char] = (magazineTable[char] || 0) + 1;
    }

    for (let char of ransomNote) {
        if (!magazineTable[char] || magazineTable[char] === 0) {
            return false; // Character not found or count exhausted
        }
        magazineTable[char]--;
    }

    return true; // Ransom note can be constructed
}

// Test the function
console.log(canConstructRansomNote("aa", "ab")); // Output: false
console.log(canConstructRansomNote("aa", "aab")); // Output: true