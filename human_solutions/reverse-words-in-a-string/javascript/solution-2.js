// Trim extra spaces, split the string into words, filter out empty strings, reverse the words, and join them back
return s.trim().split(/\s+/).filter(word => word.length > 0).reverse().join(" ");