// Create data structures
let stack = [];
let seen = new Set();
let lastIndex = new Map();

// Populate last index map
for (let i = 0; i < s.length; i++) {
    let char = s[i];
    lastIndex.set(char, i);
}

// Iterate through characters
for (let i = 0; i < s.length; i++) {
    let char = s[i];

    // Push to stack if not seen
    if (!seen.has(char)) {
        while (stack.length > 0 && char < stack[stack.length - 1] && lastIndex.get(stack[stack.length - 1]) > i) {
            seen.delete(stack.pop());
        }
        stack.push(char);
        seen.add(char);
    }
}

// Return result
return stack.join('');