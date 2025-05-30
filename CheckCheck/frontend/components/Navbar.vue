<template>
    <div
      class="sticky z-[99999] top-0 w-full border-b bg-white/75 dark:bg-gray-900/75 backdrop-blur"
    >
      <div class="container mx-auto flex items-center justify-between py-4">
        <h1 class="text-xl font-bold">MySite</h1>
  
        <div class="flex items-center gap-4">
            <ColorModeSwitch></ColorModeSwitch>
          <UButton variant="ghost" color="gray" label="Home" />
          <UButton variant="ghost" color="gray" label="About" />
          <UButton variant="ghost" color="gray" label="Contact" />
        </div>
      </div>
    </div>
  </template>
  