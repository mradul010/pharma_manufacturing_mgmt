from erpnext.stock.doctype.quality_inspection.quality_inspection import QualityInspection

from pharma_manufacturing_mgmt.utils.settings import is_item_in_scope, is_workflow_enabled


class PharmaQualityInspection(QualityInspection):
	def validate_inspection_required(self):
		if is_workflow_enabled() and is_item_in_scope(self.item_code):
			return

		super().validate_inspection_required()
