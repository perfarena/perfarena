// Initialize an object for the hashmap
let hashmap = {};

// Loop through the input string
for (let i = 0; i < input.length; i++) {
    if (!hashmap[input[i]]) {
        hashmap[input[i]] = 1;
    } else {
        hashmap[input[i]]++;
    }
}

// Loop through the hashmap to find the first non-repeating character
for (let j = 0; j < input.length; j++) {
    if (hashmap[input[j]] === 1) {
        return j; // Return the index of the first non-repeating character
    }
}

return -1; // Return -1 if no non-repeating character found