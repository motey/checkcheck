
  <template>
  <UContainer
    :ui="{ strategy: 'override', ring: 'ring-0', padding: 'px-2 py-2 sm:px-2 sm:py-2', background: '' }"
  >
  
  <UTooltip text="Archive Checklist" :shortcuts="['⌘', 'ToDo']" :popper="{ arrow: true }">
   <UButton
    :padded="false"
    color="gray"
    variant="link"
    icon="i-heroicons-archive-box-arrow-down"
    @click.stop="archiveCheckList()"
  />
  </UTooltip>

  </UContainer>
</template>

<script setup lang="ts">
const runtimeConfig = useRuntimeConfig();
const appConfig = useAppConfig();
import { useCheckListsStore } from "@/stores/checklist";

const checkListsStore = useCheckListsStore();


const props = defineProps({
  checkListId: {
    type: String,
    required: true,
  },
});

function archiveCheckList() {
    (async () => {
        await checkListsStore.archive(props.checkListId)
    })();
}


</script>

<style scoped>

</style>
