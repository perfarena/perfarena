function reverseVowels(inputString) {
    let letters = inputString.split('');
    let i = 0;
    let j = letters.length - 1;
    const vowels = 'aeiouAEIOU';

    while (i < j) {
        let leftChar = letters[i];
        let rightChar = letters[j];

        if (!vowels.includes(leftChar)) {
            i++;
        } else if (!vowels.includes(rightChar)) {
            j--;
        } else {
            [letters[i], letters[j]] = [letters[j], letters[i]];
            i++;
            j--;
        }
    }

    return letters.join('');
}

// Test the function
const inputString = "hello";
const reversedString = reverseVowels(inputString);
console.log(reversedString); // Output: "holle"