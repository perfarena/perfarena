// Split the input string into an array of words
let words = s.split(" ");

// Initialize an empty string for the result
let result = "";

// Iterate over the words in reverse order
for (let i = words.length - 1; i >= 0; i--) {
    // Check if the word is not an empty string
    if (words[i].length !== 0) {
        // Concatenate the word to the result
        result += words[i];
        
        // Add a space if it's not the first word
        if (i !== 0) {
            result += " ";
        }
    }
}

// Return the final reversed string
return result;