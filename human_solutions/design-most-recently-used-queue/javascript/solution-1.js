function findLargestOddSubstring(num) {
       for (let i = num.length - 1; i >= 0; i--) {
           if (parseInt(num[i]) % 2 === 1) {
               return num;
           } else {
               num = num.slice(0, -1);
           }
       }
       return ""; // Return an empty string if no odd integer is found
   }