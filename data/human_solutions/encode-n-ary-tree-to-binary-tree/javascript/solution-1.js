function countJewelsInStones(jewels, stones) {
    const stonesArray = stones.split('');
    let output = 0;

    for (let i = 0; i < stonesArray.length; i++) {
        if (jewels.includes(stonesArray[i])) {
            output++;
        }
    }

    return output;
}

// Test the function
const jewels = 'aA';
const stones = 'aAAbbbb';
console.log(countJewelsInStones(jewels, stones)); // Output: 3