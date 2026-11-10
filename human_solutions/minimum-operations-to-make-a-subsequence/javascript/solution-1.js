// Create a set to get distinct values of the sentence
let s = new Set(sentence);

// Convert the set into a list and then into a string
let L = "abcdefghijklmnopqrstuvwxyz";
let ARR = Array.from(s).sort().join("");

// Return the sorted string to check if all English letters appear at least once
return ARR === L;